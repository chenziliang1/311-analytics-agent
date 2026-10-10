# -*- coding: utf-8 -*-

"""
What the UI learns about a turn besides its text: progress while it runs, and
status, warnings and downloadable tables once it is done. A widget shop.
"""

import asyncio
import json
import types

import pytest

from agent_app.chat import relay, run_agent_turn
from agent_app.turn_observer import CSV_MIN_ROWS, TurnObserver, summarize, tool_output
from agent_app.vendor.vercel_ai_sdk import RequestBody


def query(status="ok", rows=3, warning=None):
    return {
        "status": status,
        "sql": "SELECT line, COUNT(*) FROM widget GROUP BY line",
        "columns": ["line", "n"],
        "rows": [[f"L{i}", i] for i in range(rows)],
        "row_count": rows,
        "truncated": False,
        "warning": warning,
        "semantic_layer_version": "1.0.0",
    }


def test_tool_output_reads_structured_content_or_text():
    out = query()
    assert tool_output({"structuredContent": out}) == out
    assert tool_output({"structuredContent": {"result": out}}) == out
    assert tool_output({"content": [{"text": "not json"}, {"text": json.dumps(out)}]}) == out
    assert tool_output({"content": [{"text": "plain"}]}) is None
    assert tool_output("nope") is None


def test_summary_ok_with_every_result_and_a_download_past_the_threshold():
    big = query(rows=CSV_MIN_ROWS + 1, warning="Defects only.")
    summary = summarize([("warehouse_query", query()), ("warehouse_query", big), ("visualize", {"status": "ok"})])
    assert summary["status"] == "ok"
    assert summary["warnings"] == ["Defects only."]
    assert [(t["row_count"], t["download"]) for t in summary["tables"]] == [(3, False), (CSV_MIN_ROWS + 1, True)]
    assert summary["tables"][0]["columns"] == ["line", "n"]


def test_summary_error_only_when_every_query_failed():
    assert summarize([("warehouse_query", query("timeout"))])["status"] == "error"
    assert summarize([("warehouse_query", query("sql_error")), ("warehouse_query", query())])["status"] == "ok"
    # A refusal is an answer, not an error; no query at all is a chat reply.
    assert summarize([("warehouse_query", query("blocked"))])["status"] == "ok"
    assert summarize([])["status"] == "ok"


def test_summary_clarify_and_deduplicated_warnings():
    summary = summarize(
        [
            ("warehouse_query", query("needs_clarification", warning="w")),
            ("warehouse_query", query(warning="w")),
        ]
    )
    assert summary["status"] == "clarify"
    assert summary["warnings"] == ["w"]


class FakeAgent:
    """Fires the observer's hooks the way strands would, around one tool call."""

    def __init__(self, observer, output):
        self.messages = []
        self.observer = observer
        self.output = output
        observer._attach(types.SimpleNamespace(agent=self))

    def __call__(self, text):
        o = self.observer
        o._reset(None)
        o._model_call(None)
        use = {"name": "warehouse_query", "input": {"intent": "defects by line"}}
        o._tool_call(types.SimpleNamespace(tool_use=use))
        o._tool_result(types.SimpleNamespace(tool_use=use, result={"structuredContent": self.output}))
        o._model_call(None)
        self.messages.append({"role": "assistant", "content": [{"text": "Line L1 leads."}]})
        usage = {"inputTokens": 1, "outputTokens": 1}
        return types.SimpleNamespace(
            metrics=types.SimpleNamespace(agent_invocations=[types.SimpleNamespace(usage=usage)])
        )


BODY = RequestBody(
    id="c", messages=[{"id": "u", "role": "user", "parts": [{"type": "text", "text": "Defects by line?"}]}]
)


def test_the_asker_is_stamped_over_whatever_the_model_sent():
    observer = TurnObserver()
    agent = FakeAgent(observer, query())
    calls = []
    original = observer._tool_call

    def spy(event):
        original(event)
        calls.append(dict(event.tool_use["input"]))

    observer._tool_call = spy
    run_agent_turn(agent, BODY, user="octocat")
    assert calls[0]["user_id"] == "octocat"
    assert calls[0]["session_id"] == "c"
    assert calls[0]["intent"] == "defects by line"
    # Per turn: nothing is left behind for the next one.
    assert observer.identity == {}


def test_a_spoofed_user_id_is_overwritten():
    observer = TurnObserver()
    observer.identity = {"user_id": "octocat", "session_id": "c"}
    use = {"name": "warehouse_query", "input": {"intent": "x", "user_id": "admin"}}
    observer._tool_call(types.SimpleNamespace(tool_use=use))
    assert use["input"]["user_id"] == "octocat"
    other = {"name": "visualize", "input": {"chart_type": "bar"}}
    observer._tool_call(types.SimpleNamespace(tool_use=other))
    assert "user_id" not in other["input"]


def test_turn_reports_progress_and_carries_the_summary():
    observer = TurnObserver()
    agent = FakeAgent(observer, query(rows=CSV_MIN_ROWS + 5))
    assert TurnObserver.of(agent) is observer
    seen = []
    result = run_agent_turn(agent, BODY, seen.append)
    assert [e["phase"] for e in seen] == ["thinking", "warehouse_query", "answering"]
    assert seen[1]["detail"] == "defects by line"
    assert result.answer == "Line L1 leads."
    assert result.status == "ok"
    assert result.tables[0]["row_count"] == CSV_MIN_ROWS + 5
    # The listener is per turn: nothing holds on to it afterwards.
    assert observer.listener is None


def test_relay_yields_progress_then_the_result():
    def run(listener):
        listener({"phase": "thinking", "detail": None})
        listener({"phase": "answering", "detail": None})
        return "done"

    async def collect():
        return [item async for item in relay(run)]

    items = asyncio.run(collect())
    assert items[-1] == ("result", "done")
    assert [v["phase"] for k, v in items if k == "status"] == ["thinking", "answering"]


def test_relay_raises_the_turns_exception():
    def run(listener):
        raise ValueError("no warehouse")

    async def collect():
        return [item async for item in relay(run)]

    with pytest.raises(ValueError, match="no warehouse"):
        asyncio.run(collect())


def test_summary_carries_the_turns_numbers_once_each():
    from agent_app.turn_observer import TURN_VALUES

    out = {"status": "ok", "rows": [["north", 1204, 0.346, True, None], ["south", 1204, 0.3]], "row_count": 2}
    assert summarize([("warehouse_query", out)])["values"] == [1204.0, 0.346, 0.3]
    wide = {"status": "ok", "rows": [[i] for i in range(TURN_VALUES + 50)], "row_count": TURN_VALUES + 50}
    assert len(summarize([("warehouse_query", wide)])["values"]) == TURN_VALUES
    assert summarize([("visualize", {"status": "ok", "spec": {"h": 1}})])["values"] == []


class TestResultsById:
    """Every ok result is named and kept whole; the model sees a preview."""

    def test_the_result_is_named_and_the_model_sees_the_preview(self):
        from agent_app.results import PREVIEW_ROWS
        from agent_app.turn_observer import with_output

        observer = TurnObserver()
        observer._reset(None)
        big = query(rows=PREVIEW_ROWS + 20)
        use = {"name": "warehouse_query", "input": {"intent": "x"}}
        event = types.SimpleNamespace(
            tool_use=use, result={"toolUseId": "t1", "status": "success", "structuredContent": big, "content": [{"text": json.dumps(big)}]}
        )
        observer._tool_result(event)
        # Kept whole, under its id.
        [(name, kept)] = observer.results
        assert name == "warehouse_query" and kept["result_id"] == "r1" and len(kept["rows"]) == PREVIEW_ROWS + 20
        assert observer.by_id["r1"] is kept
        # The model's copy, in both places strands keeps it.
        shown = event.result["structuredContent"]
        assert shown["result_id"] == "r1" and shown["preview"] is True and len(shown["rows"]) == PREVIEW_ROWS
        assert json.loads(event.result["content"][0]["text"])["rows"] == shown["rows"]
        assert event.result["toolUseId"] == "t1"
        assert with_output("not a dict", {}) == "not a dict"

    def test_ids_count_ok_results_only_and_reset_per_turn(self):
        observer = TurnObserver()
        observer._reset(None)
        for out in (query("empty", rows=0), query(), query("sql_error"), query()):
            use = {"name": "warehouse_query", "input": {}}
            observer._tool_result(types.SimpleNamespace(tool_use=use, result={"structuredContent": out}))
        assert list(observer.by_id) == ["r1", "r2"]
        assert [o.get("result_id") for _, o in observer.results] == [None, "r1", None, "r2"]
        observer._reset(None)
        assert observer.by_id == {}

    def test_a_chart_is_bound_to_the_kept_rows_not_the_models_numbers(self):
        from agent_app.turn_observer import bind_chart

        kept = {"r1": {"columns": ["line", "n", "rate"], "rows": [["A", 3, 0.5], ["B", 1, 0.0]]}}
        use = {"name": "visualize", "input": {
            "result_id": "r1", "x": "line", "y": ["rate"], "chart_type": "bar", "title": "t",
            "categories": ["A", "B"], "series": [{"name": "rate", "values": [0.9, 0.9]}],
        }}
        observer = TurnObserver()
        observer.by_id = kept
        observer._tool_call(types.SimpleNamespace(tool_use=use))
        assert use["input"]["categories"] == ["A", "B"]
        assert use["input"]["series"] == [{"name": "rate", "values": [0.5, 0.0]}]
        assert use["input"]["data_error"] == ""
        # A single y column as a string, two columns, and case-insensitive names.
        bound = bind_chart({"result_id": "r1", "x": "LINE", "y": "n"}, kept)
        assert bound["series"] == [{"name": "n", "values": [3.0, 1.0]}]
        bound = bind_chart({"result_id": "r1", "x": "line", "y": ["n", "rate"]}, kept)
        assert [s["name"] for s in bound["series"]] == ["n", "rate"]

    def test_an_unknown_result_or_column_becomes_the_tools_error(self):
        from agent_app.turn_observer import bind_chart

        kept = {"r1": {"columns": ["line", "label"], "rows": [["A", "x"]]}}
        bound = bind_chart({"result_id": "r7", "x": "line", "y": ["n"]}, kept)
        assert bound["categories"] == [] and bound["series"] == []
        assert "no result 'r7'" in bound["data_error"] and "r1" in bound["data_error"]
        assert "column 'n' is not in the result" in bind_chart({"result_id": "r1", "x": "line", "y": ["n"]}, kept)["data_error"]
        assert "not a number" in bind_chart({"result_id": "r1", "x": "line", "y": ["label"]}, kept)["data_error"]
        assert "y names no column" in bind_chart({"result_id": "r1", "x": "line"}, kept)["data_error"]


def test_summary_tables_carry_every_result_with_its_id_and_who_downloads():
    small = {**query(rows=3), "result_id": "r1"}
    big = {**query(rows=CSV_MIN_ROWS + 1), "result_id": "r2"}
    summary = summarize([("warehouse_query", small), ("warehouse_query", big), ("warehouse_query", query("empty", rows=0))])
    assert [(t["result_id"], t["download"]) for t in summary["tables"]] == [("r1", False), ("r2", True)]
    assert summary["tables"][0]["rows"] == small["rows"]
