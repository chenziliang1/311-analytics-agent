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


def test_summary_ok_with_a_table_past_the_threshold():
    big = query(rows=CSV_MIN_ROWS + 1, warning="Defects only.")
    summary = summarize([("warehouse_query", query()), ("warehouse_query", big), ("visualize", {"status": "ok"})])
    assert summary["status"] == "ok"
    assert summary["warnings"] == ["Defects only."]
    assert [t["row_count"] for t in summary["tables"]] == [CSV_MIN_ROWS + 1]
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
