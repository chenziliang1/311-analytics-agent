# -*- coding: utf-8 -*-

"""
The warehouse query tool's logic: intent in, checked rows out.

    match the semantic layer -> ask the writer for SQL -> guard -> run -> audit

Everything it needs arrives in :class:`WarehouseDeps`, so the whole path runs
in tests against SQLite with a scripted writer, and the server module is left
with nothing to do but wire the real pieces in.
"""

import dataclasses
import time
import typing as T

import sqlalchemy as sa

from ..knowledge import cited_sources
from ..semantic_layer.api import Match, SemanticRegistry
from ..semantic_layer.fast_path import plan_fast_path
from ..sql_guard import guard_select
from ..sql_utils import fetch_rows, role_statements
from ..switches import NO_SWITCHES, Switches
from ..tenant_access import TenantAccess
from .models import CitedSource, WarehouseQueryOutput
from .sql_writer import Writer, build_request, parse_reply


@dataclasses.dataclass
class WarehouseDeps:
    engine: sa.Engine
    registry: SemanticRegistry
    #: The compact schema string, sent with each request. Empty when the
    #: writer's system prompt carries it already (see writer_system_prompt).
    schema: str
    #: Base table names a query may read, lowercase.
    allowed_tables: set[str]
    writer: Writer
    audit: T.Callable[[dict], None]
    #: What the SQL writer writes.
    dialect: str = "postgres"
    #: What the warehouse runs, when it differs; the guard transpiles.
    target_dialect: str | None = None
    max_rows: int = 5000
    timeout_seconds: int = 45
    #: Which warehouse role each signed-in user's queries run as. The default
    #: switches nothing: every query runs as the connection's own role.
    tenant_access: TenantAccess = dataclasses.field(
        default_factory=lambda: TenantAccess(internal_role=None)
    )
    #: Compile a plain metric question from the semantic layer instead of
    #: asking the writer (semantic_layer.fast_path). Off until the eval says
    #: the gate is right.
    fast_path: bool = False
    #: The operator's switches as of now (agent_app.switches): a kill switch
    #: and per-metric suspensions, checked before anything else happens.
    switches: T.Callable[[], Switches] = lambda: NO_SWITCHES


def _resolve(
    registry: SemanticRegistry,
    intent: str,
    metrics: T.Sequence[str],
    dimensions: T.Sequence[str],
    time_window: str | None,
) -> tuple[Match, list[str]]:
    """
    Text matches plus what the agent named explicitly.

    A name that is not a defined one is read as a phrase (``vocabulary_drift_level``
    finds ``vocab_drift_level`` through its synonyms). One that still matches
    nothing is returned for the audit log, not shown to the user: the intent
    already carries the question, and telling a user that a hint was ignored
    reads as a failure when the answer is fine.
    """
    match = registry.match(intent)
    unresolved = []

    def add(names, table, found, kind):
        for name in names:
            item = table.get(name)
            if item is None:
                via_text = getattr(registry.match(name.replace("_", " ")), kind)
                if not via_text:
                    unresolved.append(name)
                candidates = via_text
            else:
                candidates = [item]
            for c in candidates:
                if c not in found:
                    found.append(c)

    found_metrics = list(match.metrics)
    found_dims = list(match.dimensions)
    windows = list(match.time_windows)
    add(metrics, registry.metrics, found_metrics, "metrics")
    add(dimensions, registry.dimensions, found_dims, "dimensions")
    if time_window:
        add([time_window], registry.time_windows, windows, "time_windows")
        # An explicit window outranks whatever the text happened to mention.
        windows.sort(key=lambda w: w.name != time_window)
    return Match(found_metrics, found_dims, windows, match.glossary), unresolved


def _unread_term_tables(match: Match, tables: set[str]) -> list[tuple[str, str]]:
    """(term, table) for each matched term whose ``read_from`` the query skips."""
    return [
        (g.term, g.read_from)
        for g in match.glossary
        if g.read_from and g.read_from.lower() not in tables
    ]


def role_table_source(engine: sa.Engine, table: str) -> T.Callable[[], list[tuple[str, str]]]:
    """
    Reads ``(login, role_name)`` rows from ``table`` for a RoleDirectory.

    Read as the service connection's own role, which the loader grants SELECT
    on the table: the map is consulted before any role switch, so it cannot
    depend on one.
    """
    from .. import sql_utils
    from ..tenant_access import check_table_name

    sql = f"SELECT login, role_name FROM {check_table_name(table)}"

    def read() -> list[tuple[str, str]]:
        # Not this module's fetch_rows: that one runs the user's query, and a
        # test that scripts it must not also script the map.
        result = sql_utils.fetch_rows(engine, sql, 10_000)
        if result.error:
            raise RuntimeError(f"role table {table}: {result.error}")
        return [(str(row[0]), str(row[1])) for row in result.rows]

    return read


def _run_compiled(
    deps: WarehouseDeps, intent: str, match: Match, run_as: dict, version: str, sources: list
) -> WarehouseQueryOutput | None:
    """The fast path's result, or None when the writer should take the question."""
    plan = plan_fast_path(deps.registry, intent, match)
    if plan is None:
        return None
    guarded = guard_select(
        plan.sql,
        deps.allowed_tables,
        deps.registry.sensitive_columns,
        max_rows=deps.max_rows,
        dialect=deps.dialect,
        target_dialect=deps.target_dialect,
        redacted_columns=deps.registry.redacted_columns,
    )
    if guarded.blocked:
        return None
    result = fetch_rows(deps.engine, guarded.sql, deps.max_rows, deps.timeout_seconds, **run_as)
    if result.error or result.timed_out:
        return None
    notes = list(plan.warnings) + list(guarded.warnings)
    if result.truncated:
        notes.append(f"Only the first {deps.max_rows} rows were returned; add a filter or aggregate further.")
    if not result.rows:
        notes.append("No rows matched.")
    return WarehouseQueryOutput(
        status="ok" if result.rows else "empty",
        sql=guarded.sql,
        columns=result.columns,
        rows=result.rows,
        row_count=len(result.rows),
        truncated=result.truncated,
        warning=" ".join(notes) or None,
        semantic_layer_version=version,
        sources=sources,
        path="compiled",
    )


def switch_table_source(engine: sa.Engine, table: str) -> T.Callable[[], list[tuple]]:
    """Reads ``(name, value, reason)`` rows from ``table`` for a SwitchReader, as the service role."""
    from .. import sql_utils
    from ..tenant_access import check_table_name

    sql = f"SELECT name, value, reason FROM {check_table_name(table)}"

    def read() -> list[tuple]:
        result = sql_utils.fetch_rows(engine, sql, 1_000)
        if result.error:
            raise RuntimeError(f"switch table {table}: {result.error}")
        return [tuple(row) for row in result.rows]

    return read


def run_as_for(deps: WarehouseDeps, user_id: str | None) -> tuple[dict, str | None]:
    """
    How ``user_id``'s reads run: ``fetch_rows`` keyword arguments, or why they
    may not run at all.

    Decided before any SQL is written, by the tenant access configuration and
    whether the warehouse can switch roles at all; a user whose queries would
    have to run unscoped is refused instead.
    """
    decision = deps.tenant_access.role_for(user_id)
    if decision.refused:
        return {}, decision.refused
    if not decision.switch:
        return {}, None
    run_as = dict(role=decision.role, restore_role=deps.tenant_access.internal_role)
    try:
        role_statements(deps.engine.dialect.name, **run_as)
    except ValueError as e:
        return {}, str(e)
    return run_as, None


def answer(
    deps: WarehouseDeps,
    intent: str,
    user_role: str = "internal_analyst",
    requested_metrics: T.Sequence[str] = (),
    requested_dimensions: T.Sequence[str] = (),
    time_window: str | None = None,
    user_id: str = "",
    session_id: str = "",
) -> WarehouseQueryOutput:
    """
    Never raises: every failure is a status the agent can act on.

    :param user_role: The audience this deployment serves, for the audit log.
        Deliberately not a tool argument: a role the model fills in is a role
        the model can make up, and an audit log is only worth what it can't
        be talked into.
    :param user_id: The signed-in user. Set by the agent's TurnObserver, not
        by the model: it decides the warehouse role the query runs as, and
        goes in the audit log.
    :param session_id: The chat it was asked in, for the audit log.
    """
    started = time.monotonic()
    version = deps.registry.version
    run_as, refused = run_as_for(deps, user_id)
    match, unresolved = _resolve(
        deps.registry, intent, requested_metrics, requested_dimensions, time_window
    )
    # The definitions behind the question, whole: what the answer is written
    # and cited from.
    sources = [
        CitedSource(id=s.id, title=s.title, text=s.text, citation=s.citation)
        for s in cited_sources(deps.registry, match)
    ]
    warnings = []
    if any(d.sensitive for d in match.dimensions):
        warnings.append("Residents' personal data cannot be shown; it was left out.")

    def finish(output: WarehouseQueryOutput) -> WarehouseQueryOutput:
        if warnings:
            joined = " ".join(warnings + ([output.warning] if output.warning else []))
            output = output.model_copy(update={"warning": joined})
        deps.audit(
            {
                "user_id": user_id or "anonymous",
                "session_id": session_id or None,
                "user_role": user_role,
                # The warehouse role the query ran as; None is the connection's own.
                "query_role": run_as.get("role"),
                "intent": intent,
                "metrics": [m.name for m in match.metrics],
                "unresolved_names": unresolved,
                "status": output.status,
                "path": output.path,
                "sql": output.sql,
                "row_count": output.row_count,
                "seconds": round(time.monotonic() - started, 2),
                "semantic_layer_version": version,
            }
        )
        return output

    if refused:
        # Before the writer is asked: no SQL is written for a user who may not
        # run any, and the refusal is logged with the question.
        return finish(WarehouseQueryOutput(
            status="blocked",
            warning=f"Refused: {refused}.",
            semantic_layer_version=version,
        ))

    switches = deps.switches()
    if switches.killed:
        return finish(WarehouseQueryOutput(
            status="blocked",
            warning=f"The warehouse tool is temporarily unavailable: {switches.killed}. Try again later.",
            semantic_layer_version=version,
        ))
    for metric in match.metrics:
        if metric.name in switches.suspended:
            return finish(WarehouseQueryOutput(
                status="blocked",
                warning=(
                    f"Questions about {metric.display_name} are suspended for now: "
                    f"{switches.suspended[metric.name]}. Other metrics still work."
                ),
                semantic_layer_version=version,
            ))

    if deps.fast_path:
        compiled = _run_compiled(deps, intent, match, run_as, version, sources)
        if compiled is not None:
            return finish(compiled)
        # Anything the compiler could not do, or a compiled query that
        # failed, goes to the writer as if the fast path did not exist.

    previous_sql = previous_error = None
    # Each kind of second chance is given once (a rewrite for a skipped
    # table, twice), never traded for another's.
    repairs = rewrites = db_retries = 0
    for _ in range(5):
        request = build_request(
            intent, deps.registry, match, deps.schema, previous_sql, previous_error
        )
        try:
            reply = parse_reply(deps.writer(request))
        except Exception as e:
            return finish(WarehouseQueryOutput(
                status="sql_error",
                warning=f"The query writer failed: {type(e).__name__}: {e}",
                semantic_layer_version=version,
            ))
        if reply.refusal:
            return finish(WarehouseQueryOutput(
                status="blocked",
                warning=f"Not answerable from this warehouse: {reply.refusal}",
                semantic_layer_version=version,
            ))
        if reply.clarification:
            return finish(WarehouseQueryOutput(
                status="needs_clarification",
                clarification=reply.clarification,
                semantic_layer_version=version,
            ))
        if not reply.sql:
            return finish(WarehouseQueryOutput(
                status="sql_error",
                warning="The query writer returned no query.",
                semantic_layer_version=version,
            ))

        guarded = guard_select(
            reply.sql,
            deps.allowed_tables,
            deps.registry.sensitive_columns,
            max_rows=deps.max_rows,
            dialect=deps.dialect,
            target_dialect=deps.target_dialect,
            redacted_columns=deps.registry.redacted_columns,
        )
        if guarded.blocked and guarded.repairable and repairs == 0:
            # A typo, not a boundary: same one repair a database error gets.
            repairs += 1
            previous_sql, previous_error = reply.sql, f"Refused: {guarded.blocked_reason}"
            continue
        unread = _unread_term_tables(match, guarded.tables) if not guarded.blocked else []
        if unread:
            # The writer recomputed what a table already holds (a health
            # snapshot from service_requests). Its numbers would differ from
            # the ones customers were shown, and on the first live eval they
            # did: two rewrites, then no answer rather than a wrong one.
            if rewrites < 2:
                rewrites += 1
                previous_sql = reply.sql
                previous_error = " ".join(
                    f"A question about {term} is answered by reading {table}, "
                    f"and this query does not read it. Rewrite it to read {table}."
                    for term, table in unread
                )
                continue
            term, table = unread[0]
            return finish(WarehouseQueryOutput(
                status="blocked",
                sql=reply.sql,
                warning=(
                    f"Refused: a question about {term} is answered from {table}, and the query "
                    f"kept recomputing it instead. Ask for the {table} figures directly."
                ),
                semantic_layer_version=version,
            ))
        if guarded.blocked:
            # A boundary is never retried: a second attempt would only be the
            # writer looking for a way around it.
            return finish(WarehouseQueryOutput(
                status="blocked",
                sql=reply.sql,
                warning=f"Refused: {guarded.blocked_reason}.",
                semantic_layer_version=version,
            ))

        result = fetch_rows(deps.engine, guarded.sql, deps.max_rows, deps.timeout_seconds, **run_as)
        if result.transient:
            # The connection dropped, not the query: the same SQL, once more.
            # No writer call -- there is nothing for it to fix.
            result = fetch_rows(deps.engine, guarded.sql, deps.max_rows, deps.timeout_seconds, **run_as)
        if result.timed_out:
            return finish(WarehouseQueryOutput(
                status="timeout",
                sql=guarded.sql,
                warning=f"The query ran past {deps.timeout_seconds}s. Narrow the window or add a filter.",
                semantic_layer_version=version,
            ))
        if result.error and result.transient:
            return finish(WarehouseQueryOutput(
                status="sql_error",
                sql=guarded.sql,
                warning=f"The warehouse connection failed twice: {result.error}",
                semantic_layer_version=version,
            ))
        if result.error and db_retries == 0:
            # One repair: a compile error is usually a typo the writer can fix
            # once it sees the database's sentence.
            db_retries += 1
            previous_sql, previous_error = guarded.sql, result.error
            continue
        if result.error:
            break

        notes = list(guarded.warnings)
        if result.truncated:
            notes.append(f"Only the first {deps.max_rows} rows were returned; add a filter or aggregate further.")
        if not result.rows:
            notes.append("No rows matched.")
        return finish(WarehouseQueryOutput(
            status="ok" if result.rows else "empty",
            sql=guarded.sql,
            columns=result.columns,
            rows=result.rows,
            row_count=len(result.rows),
            truncated=result.truncated,
            warning=" ".join(notes) or None,
            semantic_layer_version=version,
            sources=sources,
        ))

    return finish(WarehouseQueryOutput(
        status="sql_error",
        sql=previous_sql,
        warning=f"The query failed twice. Last error: {previous_error}",
        semantic_layer_version=version,
    ))
