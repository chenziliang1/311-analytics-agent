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
        config=types.SimpleNamespace(agentcore_runtime_arn=None, quota_max_requests_per_ip_per_day=40),
        agent=object(),
        dynamodb_client=lambda: None,
    )
    monkeypatch.setattr(index, "one", fake_one)
    monkeypatch.setattr(index, "check_quota", lambda *a, **k: None)
    monkeypatch.setattr(index, "count_request", lambda *a, **k: None)
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


def test_a_network_past_its_daily_cap_gets_the_notice_and_no_turn(app, monkeypatch):
    """The chat needs no sign-in, so the address is what gets rationed."""
    from agent_app.quota import QuotaExceededError

    counted, turns = [], []

    def count(client, config, ip, kind="chat", **kwargs):
        counted.append((ip, kind))
        raise QuotaExceededError("Daily chat cap reached")

    monkeypatch.setattr(app, "count_request", count)
    monkeypatch.setattr(app, "run_agent_turn", lambda *a: turns.append(a))
    text = TestClient(app.app).post(
        "/api/chat", json=BODY, headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
    ).text
    assert "up to 40 questions a day" in text and "github.com/chenziliang1/311-analytics-agent" in text
    # The client's own address, first in the chain the platform wrote.
    assert counted == [("203.0.113.7", "chat")] and turns == []


def test_feedback_past_its_daily_cap_is_a_429(app, monkeypatch):
    from agent_app.quota import QuotaExceededError

    def count(client, config, ip, kind="chat", **kwargs):
        if kind == "feedback":
            raise QuotaExceededError("Daily feedback cap reached")

    monkeypatch.setattr(app, "count_request", count)
    reply = TestClient(app.app).post("/api/feedback", json={"rating": "up", "session_id": "c"})
    assert reply.status_code == 429 and reply.json()["error"] == "daily limit reached"


def test_client_ip_falls_back_to_the_socket_peer(app):
    scope = {"type": "http", "headers": [], "client": ("127.0.0.1", 5000), "method": "GET", "path": "/"}
    from fastapi import Request

    assert app.client_ip(Request(scope)) == "127.0.0.1"
    scope["headers"] = [(b"x-forwarded-for", b" 198.51.100.2 ")]
    assert app.client_ip(Request(scope)) == "198.51.100.2"


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
