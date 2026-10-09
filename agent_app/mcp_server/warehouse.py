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

from ..semantic_layer.api import Match, SemanticRegistry
from ..sql_guard import guard_select
from ..sql_utils import fetch_rows
from .models import WarehouseQueryOutput
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

    :param user_id: The signed-in user, for the audit log. Set by the agent's
        TurnObserver, not by the model.
    :param session_id: The chat it was asked in, for the audit log.
    """
    started = time.monotonic()
    version = deps.registry.version
    match, unresolved = _resolve(
        deps.registry, intent, requested_metrics, requested_dimensions, time_window
    )
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
                "intent": intent,
                "metrics": [m.name for m in match.metrics],
                "unresolved_names": unresolved,
                "status": output.status,
                "sql": output.sql,
                "row_count": output.row_count,
                "seconds": round(time.monotonic() - started, 2),
                "semantic_layer_version": version,
            }
        )
        return output

    previous_sql = previous_error = None
    for attempt in range(2):
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
        )
        if guarded.blocked and guarded.repairable and attempt == 0:
            # A typo, not a boundary: same one repair a database error gets.
            previous_sql, previous_error = reply.sql, f"Refused: {guarded.blocked_reason}"
            continue
        unread = _unread_term_tables(match, guarded.tables) if not guarded.blocked else []
        if unread and attempt == 0:
            # The writer recomputed what a table already holds (a health
            # snapshot from service_requests): its numbers would differ from
            # the ones customers were shown. One rewrite, then run whatever
            # comes back -- a recomputed answer beats no answer.
            previous_sql = reply.sql
            previous_error = " ".join(
                f"A question about {term} is answered by reading {table}, "
                f"and this query does not read it. Rewrite it to read {table}."
                for term, table in unread
            )
            continue
        if guarded.blocked:
            # A boundary is never retried: a second attempt would only be the
            # writer looking for a way around it.
            return finish(WarehouseQueryOutput(
                status="blocked",
                sql=reply.sql,
                warning=f"Refused: {guarded.blocked_reason}.",
                semantic_layer_version=version,
            ))

        result = fetch_rows(deps.engine, guarded.sql, deps.max_rows, deps.timeout_seconds)
        if result.transient:
            # The connection dropped, not the query: the same SQL, once more.
            # No writer call -- there is nothing for it to fix.
            result = fetch_rows(deps.engine, guarded.sql, deps.max_rows, deps.timeout_seconds)
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
        if result.error:
            # One repair: a compile error is usually a typo the writer can fix
            # once it sees the database's sentence.
            previous_sql, previous_error = guarded.sql, result.error
            continue

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
        ))

    return finish(WarehouseQueryOutput(
        status="sql_error",
        sql=previous_sql,
        warning=f"The query failed twice. Last error: {previous_error}",
        semantic_layer_version=version,
    ))
