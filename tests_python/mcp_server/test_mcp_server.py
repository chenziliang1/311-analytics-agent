# -*- coding: utf-8 -*-

"""
The warehouse tool end to end against SQLite, with a scripted query writer.

A fictional widget shop throughout: no test here knows what dataset the repo
serves, so none of them turns into a lie after a swap.
"""

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from agent_app.mcp_server.models import ChartSeries
from agent_app.mcp_server.server import build_server
from agent_app.mcp_server.sql_writer import build_request, parse_reply
from agent_app.mcp_server.visualize import build_chart
from agent_app.mcp_server.warehouse import WarehouseDeps, answer
from agent_app.semantic_layer.api import load_registry
from agent_app.sql_utils import new_sqlite_engine

LAYER = {
    "metrics/defect_rate.yml": """
name: defect_rate
display_name: Defect Rate
description: Share of widgets returned as defective.
base_table: widget w
formula: 1.0 * SUM(w.is_defective) / COUNT(*)
time_column: w.built_on
synonyms: [defects]
""",
    "dimensions/line.yml": """
name: line
display_name: Assembly Line
expression: w.line_code
synonyms: [lines]
""",
    "dimensions/owner_name.yml": """
name: owner_name
display_name: Owner Name
expression: o.full_name
alias: o
joins:
  w: JOIN owner o ON o.id = w.owner_id
source_column: owner.full_name
sensitive: true
""",
    "time_windows/q1.yml": """
name: q1
display_name: First Quarter
start: 2026-01-01
end: 2026-04-01
synonyms: [first quarter]
""",
    "sources/plant.yml": """
name: plant
display_name: Plant profile
description: One plant, its region and head count.
list_sql: SELECT code AS key, name AS title FROM plant ORDER BY code
read_sql: SELECT code, name, region, headcount FROM plant WHERE code = :key
""",
}


@pytest.fixture
def registry(tmp_path):
    for rel, text in LAYER.items():
        path = tmp_path / "layer" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return load_registry(tmp_path / "layer")


@pytest.fixture
def engine(tmp_path):
    engine = new_sqlite_engine(str(tmp_path / "shop.sqlite"))
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE widget (id INT, line_code TEXT, is_defective INT, built_on TEXT, owner_id INT)"
        )
        conn.exec_driver_sql("CREATE TABLE owner (id INT, full_name TEXT)")
        conn.exec_driver_sql(
            "INSERT INTO widget VALUES (1,'A',1,'2026-01-02',1),(2,'A',0,'2026-01-03',1),(3,'B',0,'2026-02-01',2)"
        )
        conn.exec_driver_sql("INSERT INTO owner VALUES (1,'Ada'),(2,'Bo')")
        conn.exec_driver_sql("CREATE TABLE plant (code TEXT, name TEXT, region TEXT, headcount INT)")
        conn.exec_driver_sql("INSERT INTO plant VALUES ('lima','Lima','south',40),('oslo','Oslo','north',120)")
    return engine


class ScriptedWriter:
    """Returns canned replies in order and records every request it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.replies.pop(0)


def sql(text):
    return f"```sql\n{text}\n```"


def make_deps(engine, registry, writer, audit_log):
    return WarehouseDeps(
        engine=engine,
        registry=registry,
        schema="widget(id, line_code, is_defective, built_on, owner_id)",
        allowed_tables={"widget", "owner"},
        writer=writer,
        audit=audit_log.append,
        dialect="sqlite",
    )


def test_ok_path_returns_rows_and_audits(engine, registry):
    writer = ScriptedWriter(
        sql("SELECT line_code AS line, 1.0 * SUM(is_defective) / COUNT(*) AS defect_rate FROM widget GROUP BY line_code ORDER BY line")
    )
    log = []
    out = answer(make_deps(engine, registry, writer, log), "defects by line in the first quarter")
    assert out.status == "ok"
    assert out.columns == ["line", "defect_rate"]
    assert out.rows == [["A", 0.5], ["B", 0.0]]
    assert "LIMIT 5001" in out.sql
    # The writer was shown the matched metric, its reference query, and the window.
    request = writer.requests[0]
    assert "Metric `defect_rate`" in request
    assert "Reference query (q1, by line)" in request
    assert "Time window `q1`" in request
    assert log[0]["status"] == "ok" and log[0]["metrics"] == ["defect_rate"]
    assert "rows" not in log[0]


def test_clarification(engine, registry):
    writer = ScriptedWriter("CLARIFY: Which line do you mean?")
    out = answer(make_deps(engine, registry, writer, []), "how is it doing")
    assert out.status == "needs_clarification"
    assert out.clarification == "Which line do you mean?"


def test_blocked_is_not_retried(engine, registry):
    writer = ScriptedWriter(sql("DELETE FROM widget"))
    out = answer(make_deps(engine, registry, writer, []), "remove the widgets")
    assert out.status == "blocked"
    assert "only SELECT" in out.warning
    assert len(writer.requests) == 1


def test_personal_data_is_stripped_and_warned(engine, registry):
    writer = ScriptedWriter(
        sql("SELECT o.full_name, COUNT(*) AS n FROM widget w JOIN owner o ON o.id = w.owner_id GROUP BY o.full_name")
    )
    out = answer(make_deps(engine, registry, writer, []), "widgets per owner name")
    assert out.status == "ok"
    assert out.columns == ["n"]
    assert "personal data" in out.warning


def test_unknown_table_gets_one_repair(engine, registry):
    """A CTE the writer forgot to define reads as an unknown table: a typo."""
    writer = ScriptedWriter(
        sql("SELECT COUNT(*) AS n FROM widgets"),
        sql("SELECT COUNT(*) AS n FROM widget"),
    )
    out = answer(make_deps(engine, registry, writer, []), "how many widgets")
    assert out.status == "ok"
    assert "unknown table 'widgets'" in writer.requests[1]


def test_sql_error_gets_one_repair(engine, registry):
    writer = ScriptedWriter(
        sql("SELECT nope FROM widget"),
        sql("SELECT COUNT(*) AS n FROM widget"),
    )
    out = answer(make_deps(engine, registry, writer, []), "how many widgets")
    assert out.status == "ok"
    assert out.rows == [[3]]
    assert "Your previous query failed" in writer.requests[1]
    assert "nope" in writer.requests[1]


def test_dropped_connection_is_retried_without_the_writer(engine, registry, monkeypatch):
    from agent_app.mcp_server import warehouse
    from agent_app.sql_utils import QueryRows

    calls = []
    real = warehouse.fetch_rows

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return QueryRows([], [], False, error="server closed the connection", transient=True)
        return real(*args, **kwargs)

    monkeypatch.setattr(warehouse, "fetch_rows", flaky)
    writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget"))
    out = answer(make_deps(engine, registry, writer, []), "how many widgets")
    assert out.status == "ok"
    assert len(calls) == 2
    assert len(writer.requests) == 1


def test_connection_down_twice_is_an_error(engine, registry, monkeypatch):
    from agent_app.mcp_server import warehouse
    from agent_app.sql_utils import QueryRows

    monkeypatch.setattr(
        warehouse, "fetch_rows",
        lambda *a, **k: QueryRows([], [], False, error="no route to host", transient=True),
    )
    writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget"))
    out = answer(make_deps(engine, registry, writer, []), "how many widgets")
    assert out.status == "sql_error"
    assert "connection failed twice" in out.warning
    assert len(writer.requests) == 1


def test_sql_error_twice_gives_up(engine, registry):
    writer = ScriptedWriter(sql("SELECT nope FROM widget"), sql("SELECT nope2 FROM widget"))
    out = answer(make_deps(engine, registry, writer, []), "how many widgets")
    assert out.status == "sql_error"
    assert "failed twice" in out.warning


def test_empty(engine, registry):
    writer = ScriptedWriter(sql("SELECT id FROM widget WHERE id > 99"))
    out = answer(make_deps(engine, registry, writer, []), "widgets after 99")
    assert out.status == "empty"
    assert "No rows matched" in out.warning


def test_truncation(engine, registry):
    writer = ScriptedWriter(sql("SELECT id FROM widget ORDER BY id"))
    deps = make_deps(engine, registry, writer, [])
    deps.max_rows = 2
    out = answer(deps, "all widgets")
    assert out.rows == [[1], [2]]
    assert out.truncated
    assert "first 2 rows" in out.warning


def test_requested_names_resolve_through_synonyms_and_unknown_ones_stay_quiet(engine, registry):
    writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget"))
    log = []
    out = answer(
        make_deps(engine, registry, writer, log),
        "how many widgets",
        requested_metrics=["defects", "made_up"],
        requested_dimensions=["assembly_line"],
        time_window="q1",
    )
    assert out.status == "ok"
    # Not shown to the user: the answer is fine, a dropped hint is not news.
    assert out.warning is None
    assert log[0]["unresolved_names"] == ["made_up"]
    assert "Metric `defect_rate`" in writer.requests[0]
    assert "Dimension `line`" in writer.requests[0]


def test_writer_exception_is_a_status(engine, registry):
    def broken(_):
        raise RuntimeError("model unavailable")

    out = answer(make_deps(engine, registry, broken, []), "anything")
    assert out.status == "sql_error"
    assert "model unavailable" in out.warning


def test_parse_reply():
    assert parse_reply("Here:\n```SQL\nSELECT 1\n```").sql == "SELECT 1"
    assert parse_reply("CLARIFY: which one?").clarification == "which one?"
    assert parse_reply("CANNOT: no such data").refusal == "no such data"
    assert parse_reply("I cannot help").sql is None


def test_parse_reply_keyword_inside_a_sql_block():
    """Models wrap the keyword lines in ```sql; that is not a query."""
    reply = parse_reply("```sql\nCLARIFY: which city?\n```")
    assert reply.sql is None
    assert reply.clarification == "which city?"


def test_refusal_is_blocked(engine, registry):
    writer = ScriptedWriter("CANNOT: owner names are personal data")
    out = answer(make_deps(engine, registry, writer, []), "list owner names")
    assert out.status == "blocked"
    assert "personal data" in out.warning


def test_build_request_without_match(registry):
    request = build_request("tell me a joke", registry, registry.match("tell me a joke"), "schema")
    assert "Nothing in this question matched" in request


def test_build_request_marks_sensitive_dimension(registry):
    match = registry.match("defects by owner name")
    request = build_request("defects by owner name", registry, match, "schema")
    assert "`owner_name` is residents' personal data" in request
    # The reference query falls back to no dimension rather than the sensitive one.
    assert "Reference query (no window, by no dimension)" in request


class TestVisualize:
    def test_ok_block_is_renderer_json(self):
        out = build_chart(
            "bar", "Defects", ["A", "B"], [ChartSeries(name="rate", values=[0.5, 0.0])], "%"
        )
        assert out.status == "ok"
        payload = json.loads(out.block.removeprefix("```chart\n").removesuffix("\n```"))
        assert payload == {
            "type": "bar",
            "title": "Defects",
            "yLabel": "%",
            "categories": ["A", "B"],
            "series": [{"name": "rate", "values": [0.5, 0.0]}],
        }

    def test_unsupported_type(self):
        out = build_chart("scatter", "x", ["A"], [ChartSeries(name="s", values=[1])])
        assert out.status == "unsupported"

    @pytest.mark.parametrize(
        "categories, series, error",
        [
            ([], [ChartSeries(name="s", values=[])], "no categories"),
            (["A"], [], "no series"),
            (["A", "B"], [ChartSeries(name="s", values=[1])], "1 values for 2 categories"),
            (["A"], [ChartSeries(name=str(i), values=[1]) for i in range(5)], "at most 4"),
            ([str(i) for i in range(25)], [ChartSeries(name="s", values=[1] * 25)], "at most 24"),
            (["A"], [ChartSeries(name="s", values=[float("nan")])], "finite"),
        ],
    )
    def test_render_errors(self, categories, series, error):
        out = build_chart("line", "t", categories, series)
        assert out.status == "render_error"
        assert error in out.error


def test_warehouse_audit_log_inserts_counts_not_values(tmp_path):
    from agent_app.mcp_server.audit import warehouse_audit_log

    # SQLite's ATTACH is per connection, so use a single-connection engine;
    # the attached database stands in for the CITYPULSE_AUDIT schema.
    engine = sa.create_engine(
        "sqlite://",
        poolclass=sa.pool.StaticPool,
        # The sink inserts from its own thread, as it does in production.
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS CITYPULSE_AUDIT")
        conn.exec_driver_sql(
            "CREATE TABLE CITYPULSE_AUDIT.QUERY_LOG (log_id, session_id, user_id, asked_at, "
            "nl_query, generated_sql, row_count, tool_name, status, latency_ms)"
        )
    write = warehouse_audit_log(engine)
    write({"user_id": "octocat", "session_id": "chat-9", "user_role": "internal_analyst",
           "intent": "how many widgets", "sql": "SELECT 1",
           "row_count": 3, "status": "ok", "seconds": 1.25}).result(timeout=5)
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT user_id, session_id, nl_query, row_count, status, latency_ms FROM CITYPULSE_AUDIT.QUERY_LOG"
        ).one()
    assert tuple(row) == ("octocat", "chat-9", "how many widgets", 3, "ok", 1250)


def test_client_disconnect_errors_are_dropped_and_others_kept():
    import logging
    import sys

    from starlette.requests import ClientDisconnect

    from agent_app.mcp_server.server import DropClientDisconnects

    def record(exc):
        try:
            raise exc
        except Exception:
            info = sys.exc_info()
        return logging.LogRecord("mcp", logging.ERROR, __file__, 1, "Error handling POST request", None, info)

    f = DropClientDisconnects()
    assert f.filter(record(ClientDisconnect())) is False
    assert f.filter(record(ValueError("real problem"))) is True
    assert f.filter(logging.LogRecord("mcp", logging.INFO, __file__, 1, "ok", None, None)) is True


def test_server_lists_and_calls_tools(engine, registry):
    """Through a real MCP client session, in memory: the contract the agent sees."""
    from mcp.shared.memory import create_connected_server_and_client_session

    writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget"))
    votes = []
    server = build_server(
        deps_factory=lambda: make_deps(engine, registry, writer, []),
        feedback_factory=lambda: votes.append,
    )

    async def run():
        async with create_connected_server_and_client_session(server) as client:
            tools = await client.list_tools()
            voted = await client.call_tool("record_feedback", {"rating": "down", "comment": "wrong total"})
            bad = await client.call_tool("record_feedback", {"rating": "meh"})
            called = await client.call_tool("warehouse_query", {"intent": "how many widgets"})
            index = await client.call_tool("list_sources", {})
            doc = await client.call_tool("read_source", {"id": "metric/defect_rate"})
            chart = await client.call_tool(
                "visualize",
                {
                    "chart_type": "line",
                    "title": "t",
                    "categories": ["A"],
                    "series": [{"name": "s", "values": [1]}],
                },
            )
            return tools, called, chart, voted, bad, index, doc

    tools, called, chart, voted, bad, index, doc = asyncio.run(run())
    assert {t.name for t in tools.tools} == {
        "warehouse_query", "list_sources", "read_source", "visualize", "record_feedback"
    }
    assert "- [plant/oslo](plant/oslo): Oslo" in index.structuredContent["index"]
    assert doc.structuredContent["citation"] == "[KB:metric/defect_rate]"
    query_args = next(t for t in tools.tools if t.name == "warehouse_query").inputSchema["properties"]
    # The model never says who is asking: identity is filled in by the system.
    assert "user_role" not in query_args
    assert voted.structuredContent["status"] == "ok"
    assert [(v.rating, v.comment, v.user_id) for v in votes] == [("down", "wrong total", "anonymous")]
    assert bad.structuredContent["status"] == "invalid"
    assert called.structuredContent["status"] == "ok"
    assert called.structuredContent["rows"] == [[3]]
    assert chart.structuredContent["status"] == "ok"


def _with_term(tmp_path, read_from="widget_summary"):
    layer = dict(LAYER)
    layer["glossary/core.yml"] = f"""
- term: line report
  definition: The precomputed row per line in widget_summary.
  read_from: {read_from}
"""
    for rel, text in layer.items():
        path = tmp_path / "termlayer" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return load_registry(tmp_path / "termlayer")


def test_term_table_skipped_gets_one_rewrite(engine, tmp_path):
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE widget_summary (line_code TEXT, defect_rate REAL)")
        conn.exec_driver_sql("INSERT INTO widget_summary VALUES ('A', 0.5)")
    writer = ScriptedWriter(
        sql("SELECT line_code, AVG(is_defective) AS defect_rate FROM widget GROUP BY line_code"),
        sql("SELECT line_code, defect_rate FROM widget_summary"),
    )
    deps = make_deps(engine, _with_term(tmp_path), writer, [])
    deps.allowed_tables = {"widget", "owner", "widget_summary"}
    out = answer(deps, "the line report for line A")
    assert out.status == "ok"
    assert "widget_summary" in out.sql
    assert "answered by reading widget_summary" in writer.requests[1]


def test_term_table_still_skipped_after_two_rewrites_is_refused(engine, tmp_path):
    """A recomputed health snapshot is a different number from the one the customer saw."""
    writer = ScriptedWriter(
        sql("SELECT line_code FROM widget"),
        sql("SELECT line_code FROM widget"),
        sql("SELECT line_code FROM widget"),
        sql("SELECT line_code FROM widget"),
    )
    out = answer(make_deps(engine, _with_term(tmp_path), writer, []), "the line report")
    assert out.status == "blocked" and "widget_summary" in out.warning
    assert len(writer.requests) == 3 and out.rows == []


def test_second_chances_are_not_traded(engine, registry):
    """A rewrite does not buy a second database repair: a query that fails twice still gives up."""
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE widget_summary (line_code TEXT, defect_rate REAL)")
    writer = ScriptedWriter(
        sql("SELECT nope FROM widget"),
        sql("SELECT nope FROM widget"),
        sql("SELECT nope FROM widget"),
    )
    deps = make_deps(engine, registry, writer, [])
    out = answer(deps, "how many widgets")
    assert out.status == "sql_error" and "failed twice" in out.warning
    assert len(writer.requests) == 2


def test_schema_can_live_in_the_system_prompt(registry):
    from agent_app.mcp_server.sql_writer import writer_system_prompt

    match = registry.match("defects")
    assert "## Database schema" not in build_request("defects", registry, match, "")
    assert writer_system_prompt("Rules.\n", "widget(id)").endswith("## Database schema\n\nwidget(id)\n")


def test_audit_records_who_asked(engine, registry):
    writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget"))
    log = []
    answer(make_deps(engine, registry, writer, log), "how many widgets", user_id="octocat", session_id="chat-9")
    assert (log[0]["user_id"], log[0]["session_id"]) == ("octocat", "chat-9")
    answer(make_deps(engine, registry, ScriptedWriter(sql("SELECT 1 AS n")), log), "one")
    assert log[1]["user_id"] == "anonymous"
    assert log[0]["query_role"] is None  # nothing configured: the connection's own role


class TestKnowledgeTools:
    """The index and a document, for one asker, over the widget shop."""

    def test_a_query_returns_the_definitions_it_used_to_cite(self, engine, registry):
        writer = ScriptedWriter(sql("SELECT line_code, AVG(is_defective) AS defect_rate FROM widget GROUP BY line_code"))
        out = answer(make_deps(engine, registry, writer, []), "defect rate per line in the first quarter")
        assert out.status == "ok"
        assert [s.id for s in out.sources] == ["metric/defect_rate", "window/q1"]
        assert out.sources[0].citation == "[KB:metric/defect_rate]"
        assert "1.0 * SUM(w.is_defective)" in out.sources[0].text

    def test_index_lists_definitions_and_the_plants_in_the_warehouse(self, engine, registry):
        from agent_app.mcp_server.knowledge import source_index

        out = source_index(make_deps(engine, registry, ScriptedWriter(), []))
        assert out.status == "ok"
        assert "- [metric/defect_rate](metric/defect_rate): Defect Rate." in out.index
        assert "- [plant/lima](plant/lima): Lima\n- [plant/oslo](plant/oslo): Oslo" in out.index

    def test_a_definition_and_an_entity_are_read_and_cited(self, engine, registry):
        from agent_app.mcp_server.knowledge import read_source

        deps = make_deps(engine, registry, ScriptedWriter(), [])
        metric = read_source(deps, "metric/defect_rate")
        assert (metric.status, metric.title, metric.citation) == ("ok", "Defect Rate", "[KB:metric/defect_rate]")
        assert "1.0 * SUM(w.is_defective)" in metric.text
        plant = read_source(deps, " plant/oslo ")
        assert (plant.status, plant.id, plant.citation) == ("ok", "plant/oslo", "[KB:plant/oslo]")
        assert "- region: north" in plant.text and "- headcount: 120" in plant.text

    @pytest.mark.parametrize("source_id", ["plant/paris", "metric/profit", "plant/oslo; drop", "nonsense", ""])
    def test_unknown_or_unreadable_documents_do_not_exist(self, engine, registry, source_id):
        from agent_app.mcp_server.knowledge import read_source

        out = read_source(make_deps(engine, registry, ScriptedWriter(), []), source_id)
        assert out.status == "not_found" and out.text is None

    def test_a_refused_user_gets_no_index_and_no_entity(self, engine, registry):
        from agent_app.mcp_server.knowledge import read_source, source_index
        from agent_app.tenant_access import TenantAccess

        deps = dataclasses.replace(
            make_deps(engine, registry, ScriptedWriter(), []),
            tenant_access=TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_PLANT_NORTH"}),
        )
        assert source_index(deps, user_id="cy").status == "blocked"
        assert read_source(deps, "plant/oslo", user_id="cy").status == "blocked"
        # Definitions are not tenant data: everyone may read them.
        assert read_source(deps, "metric/defect_rate", user_id="cy").status == "ok"


class TestFastPath:
    """A plain metric question compiled from the layer; anything else, the writer."""

    def test_off_by_default_the_writer_is_asked(self, engine, registry):
        writer = ScriptedWriter(sql("SELECT line_code, AVG(is_defective) AS defect_rate FROM widget GROUP BY line_code"))
        out = answer(make_deps(engine, registry, writer, []), "defect rate by line in the first quarter")
        assert out.path == "writer" and len(writer.requests) == 1

    def test_on_a_plain_question_never_reaches_the_writer(self, engine, registry):
        writer = ScriptedWriter()
        log = []
        deps = dataclasses.replace(make_deps(engine, registry, writer, log), fast_path=True)
        out = answer(deps, "defect rate by line in the first quarter")
        assert out.status == "ok" and out.path == "compiled"
        assert writer.requests == []
        flat = " ".join(out.sql.split())
        assert "GROUP BY w.line_code" in flat and out.columns[-1] == "defect_rate"
        assert sorted(row[0] for row in out.rows) == ["A", "B"]
        assert [s.id for s in out.sources] == ["metric/defect_rate", "window/q1"]
        assert log[0]["path"] == "compiled"

    def test_on_a_question_with_a_condition_goes_to_the_writer(self, engine, registry):
        writer = ScriptedWriter(sql("SELECT line_code, AVG(is_defective) AS defect_rate FROM widget WHERE owner_id = 1 GROUP BY line_code"))
        deps = dataclasses.replace(make_deps(engine, registry, writer, []), fast_path=True)
        out = answer(deps, "defect rate by line for Ada's widgets")
        assert out.path == "writer" and len(writer.requests) == 1

    def test_a_compiled_query_that_fails_falls_back_to_the_writer(self, engine, registry):
        """The table the layer names is not in the warehouse: the writer gets the question."""
        writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM owner"), sql("SELECT COUNT(*) AS n FROM owner"))
        deps = dataclasses.replace(make_deps(engine, registry, writer, []), fast_path=True, allowed_tables={"owner"})
        out = answer(deps, "the overall defect rate")
        assert out.status == "ok" and out.path == "writer" and len(writer.requests) >= 1


class TestTenantScope:
    """The warehouse role each user's query runs as, with the switch itself scripted."""

    @staticmethod
    def scoped_deps(engine, registry, writer, log, **access):
        from agent_app.tenant_access import TenantAccess

        deps = make_deps(engine, registry, writer, log)
        return dataclasses.replace(deps, tenant_access=TenantAccess(internal_role="SHOP_RO", **access))

    @pytest.fixture
    def fetches(self, monkeypatch):
        """Record what fetch_rows is asked to run as, without a warehouse that has roles."""
        from agent_app.mcp_server import warehouse
        from agent_app.sql_utils import QueryRows

        calls = []

        def fake(engine, sql, max_rows, timeout_seconds=None, role=None, restore_role=None):
            calls.append((role, restore_role))
            return QueryRows(["n"], [[3]], False)

        monkeypatch.setattr(warehouse, "fetch_rows", fake)
        # SQLite has no roles; pretend the warehouse can switch (the real check
        # has its own test below).
        monkeypatch.setattr(warehouse, "role_statements", lambda dialect, role, restore_role: ([], []))
        return calls

    def test_a_mapped_user_runs_as_their_role_and_the_connection_is_restored(self, engine, registry, fetches):
        log = []
        deps = self.scoped_deps(engine, registry, ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget")), log,
                                user_roles={"ada": "SHOP_PLANT_NORTH"})
        out = answer(deps, "how many widgets", user_id="ada")
        assert out.status == "ok" and out.rows == [[3]]
        assert fetches == [("SHOP_PLANT_NORTH", "SHOP_RO")]
        assert log[0]["query_role"] == "SHOP_PLANT_NORTH"

    def test_an_internal_user_switches_nothing(self, engine, registry, fetches):
        deps = self.scoped_deps(engine, registry, ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget")), [],
                                user_roles={"bo": "SHOP_RO"})
        answer(deps, "how many widgets", user_id="bo")
        assert fetches == [(None, None)]

    def test_an_unknown_user_is_refused_before_any_sql_is_written(self, engine, registry, fetches):
        writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget"))
        log = []
        deps = self.scoped_deps(engine, registry, writer, log, user_roles={"ada": "SHOP_PLANT_NORTH"})
        out = answer(deps, "how many widgets", user_id="cy", session_id="chat-1")
        assert out.status == "blocked" and "no tenant access" in out.warning and out.sql is None
        assert writer.requests == [] and fetches == []
        assert (log[0]["status"], log[0]["user_id"], log[0]["query_role"]) == ("blocked", "cy", None)
        # Sign-in off, or a session with no user, is not an internal analyst either.
        assert answer(deps, "how many widgets").status == "blocked"

    def test_the_map_can_come_from_a_warehouse_table(self, engine, registry, fetches):
        """One row in the table maps a user; the directory reads it as the service role."""
        from agent_app.mcp_server.warehouse import role_table_source
        from agent_app.tenant_access import RoleDirectory, TenantAccess

        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE user_role_access (login TEXT, role_name TEXT)")
            conn.exec_driver_sql("INSERT INTO user_role_access VALUES ('ada', 'SHOP_PLANT_NORTH')")
        directory = RoleDirectory(role_table_source(engine, "user_role_access"), overrides={"bo": "SHOP_RO"})
        deps = dataclasses.replace(
            make_deps(engine, registry, ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget")), []),
            tenant_access=TenantAccess(internal_role="SHOP_RO", user_roles=directory),
        )
        assert answer(deps, "how many widgets", user_id="ada").status == "ok"
        assert fetches[-1] == ("SHOP_PLANT_NORTH", "SHOP_RO")
        assert answer(deps, "how many widgets", user_id="cy").status == "blocked"

    def test_a_role_table_that_is_not_a_table_name_is_refused(self, engine):
        from agent_app.mcp_server.warehouse import role_table_source

        with pytest.raises(ValueError, match="table name"):
            role_table_source(engine, "users; DROP TABLE widget")

    def test_a_scoped_query_the_warehouse_cannot_switch_is_refused_before_the_writer(self, engine, registry):
        """SQLite has no roles: refused, not run unscoped, and no SQL written for it."""
        writer = ScriptedWriter(sql("SELECT COUNT(*) AS n FROM widget"))
        deps = self.scoped_deps(engine, registry, writer, [], default_role="SHOP_GUEST")
        out = answer(deps, "how many widgets", user_id="anyone")
        assert out.status == "blocked" and out.rows == [] and "no roles" in out.warning
        assert writer.requests == []
