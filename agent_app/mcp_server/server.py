# -*- coding: utf-8 -*-

"""
The MCP server the agent's data tools are served from.

Two transports, one server. Locally the agent starts it as a child process and
talks over stdio, so nothing listens on a port. Deployed, it runs over
streamable HTTP as its own service (an AgentCore Runtime MCP endpoint), which
is what lets the warehouse credentials live with the tool rather than with the
agent.

**Nothing in this process may write to stdout** under stdio: that stream is the
protocol. Diagnostics go to stderr.
"""

import functools
import logging
import threading

from mcp.server.fastmcp import FastMCP

from .feedback import FEEDBACK_TOOL, Feedback, FeedbackSink
from .knowledge import read_source as read_source_document
from .knowledge import source_index
from .models import (
    ChartSeries,
    FeedbackOutput,
    SourceIndexOutput,
    SourceOutput,
    VisualizeOutput,
    WarehouseQueryOutput,
)
from .visualize import build_chart
from .warehouse import WarehouseDeps, answer
from ..switches import NO_SWITCHES

INSTRUCTIONS = """\
Read-only analytics over a 311 service request warehouse for 120 North American
cities and counties. Call warehouse_query for any number, count, rate or ranking;
it writes, checks and runs the SQL, and returns the ids of the definitions it
used. Call read_source to read a definition before saying what a number means,
and cite it. Call list_sources to find an id you do not have. Call visualize to
turn a result into a chart block the user's browser draws.
"""


class DropClientDisconnects(logging.Filter):
    """
    Drop the transport's error record for a client that hung up mid-request.

    Behind AgentCore Runtime the MCP client's session teardown arrives as
    POSTs whose connection closes before the body is read. The SDK logs each
    as ERROR with a full traceback -- dozens of lines per question -- while
    the tool call itself has already succeeded. Only that one exception type
    is dropped; every other error still reaches the log.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return type(exc).__name__ != "ClientDisconnect"


logging.getLogger("mcp.server.streamable_http").addFilter(DropClientDisconnects())


_DEPS_LOCK = threading.Lock()


def default_deps() -> WarehouseDeps:  # pragma: no cover - needs a database and a model
    """
    The real pieces, built once rather than at import. Locked, because the
    warm-up thread and the first tool call can both ask while it is building,
    and it should be built once.
    """
    with _DEPS_LOCK:
        return _build_default_deps()


@functools.cache
def _build_default_deps() -> WarehouseDeps:  # pragma: no cover - needs a database and a model
    import sqlalchemy as sa

    from ..api import one
    from ..metrics import query_metrics_sink
    from ..paths import path_enum
    from ..semantic_layer.api import load_registry
    from .audit import fan_out, jsonl_audit_log, stderr_audit_log, warehouse_audit_log
    from .sql_writer import model_writer, writer_system_prompt

    allowed = {
        name.lower()
        for name in sa.inspect(one.engine).get_table_names(schema=one.warehouse_schema)
    }
    metrics = one.metrics("mcp")

    def on_usage(input_tokens: int, output_tokens: int) -> None:
        metrics.put("InputTokens", input_tokens)
        metrics.put("OutputTokens", output_tokens)

    tenant_access = one.config.tenant_access
    if one.config.tenant_role_table:
        import dataclasses
        import sys

        from ..tenant_access import RoleDirectory
        from .warehouse import role_table_source

        tenant_access = dataclasses.replace(
            tenant_access,
            user_roles=RoleDirectory(
                role_table_source(one.engine, one.config.tenant_role_table),
                overrides=one.config.tenant_role_map,
                on_error=lambda e: print(f"[TenantAccess] role table read failed: {e}", file=sys.stderr),
            ),
        )

    switches = lambda: NO_SWITCHES  # noqa: E731
    if one.config.switch_table:
        import sys

        from ..switches import SwitchReader
        from .warehouse import switch_table_source

        switches = SwitchReader(
            switch_table_source(one.engine, one.config.switch_table),
            ttl_seconds=one.config.switch_ttl_seconds,
            on_error=lambda e: print(f"[Switches] read failed: {e}", file=sys.stderr),
        )

    return WarehouseDeps(
        engine=one.engine,
        registry=load_registry(path_enum.dir_semantic_layer),
        switches=switches,
        # In the writer's cached system prompt instead, below.
        schema="",
        allowed_tables=allowed,
        dialect="postgres",
        target_dialect=one.sql_dialect,
        tenant_access=tenant_access,
        fast_path=one.config.fast_path,
        writer=model_writer(
            one.sql_writer_model,
            writer_system_prompt(
                path_enum.path_sql_writer_prompt_md.read_text(encoding="utf-8"),
                one.database_schema_str,
            ),
            on_usage=on_usage,
        ),
        audit=fan_out(
            jsonl_audit_log(path_enum.path_query_log_jsonl),
            stderr_audit_log(),
            query_metrics_sink(metrics),
            # QUERY_LOG lives in Snowflake; PostgreSQL has no
            # writable audit schema here, so the log files cover it there.
            *([warehouse_audit_log(one.engine)] if one.config.warehouse == "snowflake" else []),
        ),
    )


def warm_up(deps_factory=default_deps) -> threading.Thread:
    """
    Build the tool's dependencies in the background, before the first call.

    On a cold runtime the first question otherwise waits for all of it in
    series: the warehouse connection, the schema read (4s on Snowflake) and the
    table list. Started at process start, it overlaps with the agent's own
    first model call, which takes about as long. A failure is left for the
    first real call to report: that one has a user waiting on an answer.
    """

    def run() -> None:
        try:
            deps = deps_factory()
            with deps.engine.connect() as conn:
                # Wakes a suspended Snowflake warehouse too.
                conn.exec_driver_sql("SELECT 1")
        except Exception as e:  # pragma: no cover - reported by the first call
            logging.getLogger(__name__).warning("warm-up failed: %s: %s", type(e).__name__, e)

    thread = threading.Thread(target=run, name="mcp-warm-up", daemon=True)
    thread.start()
    return thread


@functools.cache
def default_feedback_sink() -> FeedbackSink:  # pragma: no cover - needs a database
    """The jsonl file always; the FEEDBACK table when the warehouse is Snowflake."""
    from ..api import one
    from ..paths import path_enum
    from .feedback import fan_out_feedback, jsonl_feedback_log, warehouse_feedback_log

    sinks = [jsonl_feedback_log(path_enum.path_feedback_jsonl)]
    if one.config.warehouse == "snowflake":
        sinks.append(warehouse_feedback_log(one.engine))
    return fan_out_feedback(*sinks)


def build_server(deps_factory=default_deps, feedback_factory=default_feedback_sink, **settings) -> FastMCP:
    """
    :param deps_factory: Returns the :class:`WarehouseDeps`. Called on each
        tool call; the default caches, a test passes its own.
    :param feedback_factory: Returns where record_feedback writes.
    :param settings: Passed to FastMCP, e.g. ``host``, ``port``, ``stateless_http``.
    """
    server = FastMCP("citypulse-warehouse", instructions=INSTRUCTIONS, **settings)

    @server.tool(name="warehouse_query")
    def warehouse_query(
        intent: str,
        requested_metrics: list[str] | None = None,
        requested_dimensions: list[str] | None = None,
        time_window: str | None = None,
        user_id: str = "",
        session_id: str = "",
    ) -> WarehouseQueryOutput:
        """
        Answer one analytics question from the warehouse. The tool writes the
        SQL itself from the defined metrics, checks it is read-only, runs it,
        and returns the rows.

        Args:
            intent: The question as one self-contained sentence. Resolve
                follow-ups first: "break that down by size tier" must become
                "SLA breach rate over the last 30 days by size tier". Keep
                the user's own words for what is counted and what it is
                divided by ("what share of all those requests breached",
                "including open requests"): renaming that to a metric
                ("breach rate") swaps in the metric's default denominator.
            requested_metrics: Metric names, when you know them (e.g.
                sla_breach_rate). Optional; the intent is matched too.
            requested_dimensions: Dimension names to group by (e.g. tenant,
                size_tier, channel, month). Optional.
            time_window: trailing_12m, rolling_30d, current_quarter,
                prior_quarter or all_history. Optional.
            user_id: Leave empty: filled in by the system for the audit log.
            session_id: Leave empty: filled in by the system for the audit log.

        Returns status ok with columns and rows, or: empty (no rows matched),
        needs_clarification (ask the user the returned clarification),
        blocked (refused as unsafe, as personal data, or as not answerable
        from this warehouse), timeout, sql_error.
        Pass any warning on to the user.
        """
        return answer(
            deps_factory(),
            intent=intent,
            requested_metrics=requested_metrics or (),
            requested_dimensions=requested_dimensions or (),
            time_window=time_window,
            user_id=user_id,
            session_id=session_id,
        )

    @server.tool(name="list_sources")
    def list_sources(user_id: str = "") -> SourceIndexOutput:
        """
        The knowledge base index: one line per approved definition (metrics,
        terms, time windows, dimensions) and per entity the asker may read,
        each with its id. Read it to find an id you do not have; for a number
        you just queried, warehouse_query already returned the ids to cite.

        Args:
            user_id: Leave empty: filled in by the system.

        Returns status ok with the index, or blocked.
        """
        return source_index(deps_factory(), user_id=user_id)

    @server.tool(name="read_source")
    def read_source(id: str, user_id: str = "") -> SourceOutput:
        """
        One knowledge base document: what a metric means and how it is
        computed, what a term means, what a time window covers, or an
        entity's profile (e.g. tenant/austin_tx). Read the definition behind
        a number before you say how it was computed, and put the returned
        citation marker, exactly, at the end of that line.

        Args:
            id: A source id as returned by warehouse_query or listed by
                list_sources, e.g. metric/sla_breach_rate.
            user_id: Leave empty: filled in by the system.

        Returns status ok with title, text and citation; not_found when there
        is no such document for you; blocked.
        """
        return read_source_document(deps_factory(), id, user_id=user_id)

    @server.tool(name="visualize")
    def visualize(
        chart_type: str,
        title: str,
        categories: list[str],
        series: list[ChartSeries],
        y_label: str = "",
    ) -> VisualizeOutput:
        """
        Check chart data and return a ```chart block for the user's browser to
        draw. Paste the returned block into your answer exactly as given.

        Args:
            chart_type: "bar" to compare categories, "line" for change over an
                ordered scale such as months.
            title: The chart title.
            categories: The x-axis labels, at most 24.
            series: At most 4, each with a name and exactly one number per
                category. Plain numbers: put the unit in y_label.
            y_label: The unit, e.g. "%".

        Returns status ok with the block, unsupported (show a Markdown table
        instead), or render_error with what to fix.
        """
        return build_chart(chart_type, title, categories, series, y_label)

    @server.tool(name=FEEDBACK_TOOL)
    def record_feedback(
        rating: str,
        session_id: str = "",
        message_id: str = "",
        user_id: str = "",
        comment: str | None = None,
        question: str = "",
        answer: str = "",
    ) -> FeedbackOutput:
        """
        Store a user's thumbs up or down on one answer. Called by the web app,
        not by the agent: the agent's MCP client filters this tool out.

        Args:
            rating: up or down.
            session_id: The chat's id.
            message_id: The rated answer's id in that chat.
            user_id: The signed-in user, set by the web app.
            comment: Optional, what was wrong.
            question: The question the answer replied to.
            answer: The answer that was rated.
        """
        try:
            fb = Feedback.new(rating, session_id, message_id, user_id, comment, question, answer)
        except ValueError as e:
            return FeedbackOutput(status="invalid", error=str(e))
        try:
            feedback_factory()(fb)
        except Exception as e:
            return FeedbackOutput(status="error", error=f"{type(e).__name__}: {e}")
        return FeedbackOutput(status="ok", feedback_id=fb.feedback_id)

    return server
