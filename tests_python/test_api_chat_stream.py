# -*- coding: utf-8 -*-

"""
/api/chat end to end with the agent replaced: progress streams first as
transient parts, then the answer and the turn's data parts; a failed turn
ends in the protocol's error event.
"""

import json
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_app.chat import ChatResult

BODY = {"id": "c", "messages": [{"id": "u", "role": "user", "parts": [{"type": "text", "text": "Widgets?"}]}]}


@pytest.fixture
def app(monkeypatch):
    # The web app is the repo's top-level api/, outside the package.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from api import index

    fake_one = types.SimpleNamespace(
        config=types.SimpleNamespace(agentcore_runtime_arn=None),
        agent=object(),
        dynamodb_client=lambda: None,
    )
    monkeypatch.setattr(index, "one", fake_one)
    monkeypatch.setattr(index, "check_quota", lambda *a, **k: None)
    monkeypatch.setattr(index, "increment_usage", lambda *a, **k: None)
    return index


def events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {")]


def test_progress_then_answer_then_data_parts(app, monkeypatch):
    users = []

    def turn(agent, body, listener, user):
        users.append(user)
        listener({"phase": "warehouse_query", "detail": "widgets"})
        return ChatResult(
            "3 widgets.", "", 1, 1, status="ok", warnings=["w"], tables=[{"columns": ["a"], "rows": [[1]]}],
            unverified_numbers=["42"], values=[3.0],
        )

    monkeypatch.setattr(app, "run_agent_turn", turn)
    got = events(TestClient(app.app).post("/api/chat", json=BODY, headers={"x-lens-user": "octocat"}).text)
    assert users == ["octocat"]
    types_ = [e["type"] for e in got]
    assert types_[0] == "data-status" and got[0]["transient"] is True
    assert types_.index("text-delta") < types_.index("data-turn") < types_.index("data-table") < types_.index("finish")
    assert got[types_.index("data-turn")]["data"] == {"status": "ok", "warnings": ["w"], "unverified": ["42"], "values": [3.0]}


def test_failed_turn_is_an_error_event(app, monkeypatch):
    def turn(agent, body, listener, user):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(app, "run_agent_turn", turn)
    got = events(TestClient(app.app).post("/api/chat", json=BODY).text)
    assert got == [{"type": "error", "errorText": "The agent failed: RuntimeError"}]


def test_feedback_is_recorded_with_the_signed_in_user(app, monkeypatch):
    calls = []

    def record(mcp_client, arguments):
        calls.append(arguments)
        return {"status": "ok", "feedback_id": "f1"}

    monkeypatch.setattr(app, "record_feedback_via_mcp", record)
    app.one.mcp_client = object()
    client = TestClient(app.app)
    reply = client.post(
        "/api/feedback",
        json={"rating": "down", "session_id": "c", "comment": "off by one"},
        headers={"x-lens-user": "octocat"},
    )
    assert reply.json() == {"status": "ok", "feedback_id": "f1"}
    assert calls[0]["user_id"] == "octocat" and calls[0]["comment"] == "off by one"
    assert client.post("/api/feedback", json={"rating": "meh"}).status_code == 400


@pytest.mark.parametrize(
    "status, http",
    [("ok", 200), ("not_found", 404), ("blocked", 403), ("error", 502)],
)
def test_a_citations_document_is_read_as_the_signed_in_user(app, monkeypatch, status, http):
    reads = []

    def read(mcp_client, source_id, user):
        reads.append((source_id, user))
        return {"status": status, "id": source_id, "title": "Defect Rate"}

    monkeypatch.setattr(app, "read_source_via_mcp", read)
    app.one.mcp_client = object()
    reply = TestClient(app.app).get("/api/source/metric/defect_rate?chat=c", headers={"x-lens-user": "octocat"})
    assert reply.status_code == http and reply.json()["id"] == "metric/defect_rate"
    assert reads == [("metric/defect_rate", "octocat")]
