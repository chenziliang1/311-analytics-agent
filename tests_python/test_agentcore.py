# -*- coding: utf-8 -*-

"""
The deployment pieces, with every AWS call replaced: the container app, the
remote turn, SigV4 signing, secrets, metrics.
"""

import io
import json
import types

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_app.agentcore_app import build_app
from agent_app.aws_auth import SigV4Auth, agentcore_invocation_url, load_secrets_into_env
from agent_app.chat import ChatResult, invoke_remote_turn, runtime_session_id
from agent_app.metrics import Metrics, NoMetrics, query_metrics_sink

BODY = {
    "id": "chat-1",
    "messages": [{"id": "u1", "role": "user", "parts": [{"type": "text", "text": "How many widgets?"}]}],
}


class FakeAgent:
    """Answers with fixed text and records the history it was handed."""

    def __init__(self):
        self.messages = []
        self.asked = []

    def __call__(self, text):
        self.asked.append(text)
        self.messages.append({"role": "assistant", "content": [{"text": "There are 3 widgets."}]})
        usage = {"inputTokens": 120, "outputTokens": 30}
        return types.SimpleNamespace(
            metrics=types.SimpleNamespace(agent_invocations=[types.SimpleNamespace(usage=usage)])
        )


class RecordingMetrics:
    def __init__(self):
        self.points = []

    def put(self, name, value, unit="Count", dimensions=None):
        self.points.append((name, value))


def test_ping():
    assert TestClient(build_app(FakeAgent, NoMetrics())).get("/ping").json() == {"status": "Healthy"}


def test_invocations_runs_a_turn_and_reports_metrics():
    agent, metrics = FakeAgent(), RecordingMetrics()
    client = TestClient(build_app(lambda: agent, metrics))
    reply = client.post("/invocations", json=BODY).json()
    assert reply["answer"] == "There are 3 widgets."
    assert (reply["input_tokens"], reply["output_tokens"]) == (120, 30)
    assert agent.asked == ["How many widgets?"]
    names = [n for n, _ in metrics.points]
    assert names[:2] == ["ColdStartTurns", "Turns"]
    client.post("/invocations", json=BODY)
    assert [n for n, _ in metrics.points].count("ColdStartTurns") == 1


def test_invocations_rejects_a_body_without_a_question():
    client = TestClient(build_app(FakeAgent, NoMetrics()))
    assert client.post("/invocations", json={"id": "x", "messages": []}).status_code == 400
    assert client.post("/invocations", content=b"not json").status_code == 400


def test_remote_turn_forwards_the_body_and_reads_the_reply():
    sent = {}

    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            sent.update(kwargs)
            payload = ChatResult("ok", "thought", 5, 6).to_json()
            return {"response": io.BytesIO(json.dumps(payload).encode())}

    spoofed = {**BODY, "lens_user": "admin"}
    result = invoke_remote_turn(FakeClient(), "arn:aws:bedrock-agentcore:r:1:runtime/a", spoofed, user="octocat")
    assert result == ChatResult("ok", "thought", 5, 6)
    # The browser's body, with the signed-in user set over any it claimed.
    assert json.loads(sent["payload"]) == {**BODY, "lens_user": "octocat"}
    assert sent["runtimeSessionId"] == runtime_session_id("chat-1")


def test_session_id_is_stable_long_enough_and_per_chat():
    a = runtime_session_id("chat-1")
    assert a == runtime_session_id("chat-1") and len(a) >= 33
    assert a != runtime_session_id("chat-2")


def test_invocation_url_escapes_the_whole_arn():
    url = agentcore_invocation_url("arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp-x", "us-east-1")
    assert url == (
        "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
        "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123%3Aruntime%2Fmcp-x/invocations?qualifier=DEFAULT"
    )


def test_sigv4_signs_the_request():
    from botocore.credentials import Credentials

    session = types.SimpleNamespace(get_credentials=lambda: Credentials("AKIDEXAMPLE", "secret", "token"))
    request = httpx.Request(
        "POST", "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/x/invocations",
        content=b'{"jsonrpc": "2.0"}', headers={"content-type": "application/json"},
    )
    signed = next(SigV4Auth(session, "us-east-1").auth_flow(request))
    assert signed.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-1/bedrock-agentcore/aws4_request" in signed.headers["authorization"]
    assert signed.headers["x-amz-security-token"] == "token"


def test_secrets_fill_only_unset_variables(monkeypatch):
    monkeypatch.setenv("ACME_KEEP", "local")
    monkeypatch.delenv("ACME_NEW", raising=False)

    class FakeSecrets:
        def get_secret_value(self, SecretId):
            return {"SecretString": json.dumps({"ACME_KEEP": "remote", "ACME_NEW": "remote", "ACME_EMPTY": ""})}

    names = load_secrets_into_env(" acme/one ", client=FakeSecrets())
    import os

    assert os.environ["ACME_KEEP"] == "local"
    assert os.environ["ACME_NEW"] == "remote"
    assert names == ["ACME_NEW"]
    monkeypatch.delenv("ACME_NEW")


def test_metrics_publish_in_the_background_with_the_component():
    calls = []
    client = types.SimpleNamespace(put_metric_data=lambda **kw: calls.append(kw))
    m = Metrics(lambda: client, "Acme", "mcp")
    m.put("WarehouseQueries", 1, dimensions={"Status": "ok"}).result(timeout=5)
    datum = calls[0]["MetricData"][0]
    assert calls[0]["Namespace"] == "Acme"
    assert datum["Dimensions"] == [{"Name": "Component", "Value": "mcp"}, {"Name": "Status", "Value": "ok"}]


def test_query_metrics_sink():
    m = RecordingMetrics()
    query_metrics_sink(m)({"status": "blocked", "seconds": 1.5})
    assert m.points == [("WarehouseQueries", 1), ("WarehouseQueryLatency", 1.5)]


def _sse_events(text):
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


def test_invocations_streams_when_asked():
    agent, metrics = FakeAgent(), RecordingMetrics()
    client = TestClient(build_app(lambda: agent, metrics))
    reply = client.post("/invocations", json=BODY, headers={"accept": "text/event-stream"})
    assert reply.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(reply.text)
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["answer"] == "There are 3 widgets."
    assert "Turns" in [n for n, _ in metrics.points]


def test_invocations_stream_reports_a_failed_turn():
    def broken():
        raise RuntimeError("model down")

    client = TestClient(build_app(broken, NoMetrics()))
    events = _sse_events(client.post("/invocations", json=BODY, headers={"accept": "text/event-stream"}).text)
    assert events == [{"type": "error", "message": "RuntimeError: model down"}]


class FakeStream:
    def __init__(self, lines):
        self.lines = lines

    def iter_lines(self):
        yield from self.lines


def test_remote_turn_relays_progress_then_the_result():
    result = ChatResult("ok", "", 1, 2, status="clarify", warnings=["w"])

    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            assert kwargs["accept"] == "text/event-stream"
            return {
                "contentType": "text/event-stream",
                "response": FakeStream([
                    b'data: {"type": "status", "data": {"phase": "thinking", "detail": null}}',
                    b"",
                    b"data: " + json.dumps({"type": "result", "data": result.to_json()}).encode(),
                ]),
            }

    seen = []
    got = invoke_remote_turn(FakeClient(), "arn:aws:bedrock-agentcore:r:1:runtime/a", BODY, seen.append)
    assert got == result
    assert seen == [{"phase": "thinking", "detail": None}]


def test_remote_turn_error_event_raises():
    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            return {"contentType": "text/event-stream", "response": FakeStream([b'data: {"type": "error", "message": "boom"}'])}

    with pytest.raises(RuntimeError, match="boom"):
        invoke_remote_turn(FakeClient(), "arn:aws:bedrock-agentcore:r:1:runtime/a", BODY)


def test_remote_turn_reads_events_as_they_arrive():
    """Small reads, split mid-line, as a network delivers them."""
    result = ChatResult("ok", "")
    payload = (
        b'data: {"type": "status", "data": {"phase": "thinking", "detail": null}}\n\n'
        + b"data: " + json.dumps({"type": "result", "data": result.to_json()}).encode() + b"\n\n"
    )

    class Raw:
        def __init__(self):
            self.chunks = [payload[i:i + 7] for i in range(0, len(payload), 7)]

        def read1(self, amt):
            return self.chunks.pop(0) if self.chunks else b""

    class Body:
        _raw_stream = Raw()

    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            return {"contentType": "text/event-stream", "response": Body()}

    seen = []
    assert invoke_remote_turn(FakeClient(), "arn:aws:bedrock-agentcore:r:1:runtime/a", BODY, seen.append) == result
    assert seen == [{"phase": "thinking", "detail": None}]


def test_a_vote_is_recorded_not_answered():
    recorded = []

    def recorder(arguments):
        recorded.append(arguments)
        return {"status": "ok", "feedback_id": "f1"}

    agent = FakeAgent()
    client = TestClient(build_app(lambda: agent, NoMetrics(), feedback_recorder=recorder))
    vote = {"rating": "down", "session_id": "chat-1", "comment": "wrong", "user_id": "admin", "extra": "x"}
    reply = client.post("/invocations", json={"lens_feedback": vote, "lens_user": "octocat"})
    assert reply.json() == {"status": "ok", "feedback_id": "f1"}
    # The user is the signed-in one, never the vote's own field; unknown keys drop.
    assert recorded == [{"rating": "down", "session_id": "chat-1", "comment": "wrong", "user_id": "octocat"}]
    assert agent.asked == []


def test_remote_feedback_goes_to_the_chats_session():
    sent = {}

    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            sent.update(kwargs)
            return {"response": io.BytesIO(b'{"status": "ok", "feedback_id": "f1"}')}

    from agent_app.chat import invoke_remote_feedback

    out = invoke_remote_feedback(FakeClient(), "arn:aws:bedrock-agentcore:r:1:runtime/a", {"rating": "up", "session_id": "chat-1"}, "octocat")
    assert out["status"] == "ok"
    assert sent["runtimeSessionId"] == runtime_session_id("chat-1")
    assert json.loads(sent["payload"]) == {"lens_feedback": {"rating": "up", "session_id": "chat-1"}, "lens_user": "octocat"}


def test_a_source_read_is_answered_not_asked():
    """A citation opened in the browser: read as the signed-in user, never run as a turn."""
    reads = []

    def reader(source_id, user):
        reads.append((source_id, user))
        return {"status": "ok", "id": source_id, "title": "Defect Rate", "text": "# Defect Rate\n\n...", "citation": f"[KB:{source_id}]"}

    agent = FakeAgent()
    client = TestClient(build_app(lambda: agent, NoMetrics(), source_reader=reader))
    reply = client.post("/invocations", json={"lens_source": {"id": "metric/defect_rate"}, "lens_user": "octocat"})
    assert reply.status_code == 200 and reply.json()["title"] == "Defect Rate"
    assert reads == [("metric/defect_rate", "octocat")] and agent.asked == []


def test_remote_source_read_uses_the_chats_session():
    sent = {}

    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            sent.update(kwargs)
            return {"response": io.BytesIO(b'{"status": "not_found", "id": "plant/paris"}')}

    from agent_app.chat import invoke_remote_source

    out = invoke_remote_source(FakeClient(), "arn:aws:bedrock-agentcore:r:1:runtime/a", "plant/paris", "chat-1", "octocat")
    assert out["status"] == "not_found"
    assert sent["runtimeSessionId"] == runtime_session_id("chat-1")
    assert json.loads(sent["payload"]) == {"lens_source": {"id": "plant/paris"}, "lens_user": "octocat"}


def test_read_source_via_mcp_passes_the_user_and_unwraps_the_output():
    from agent_app.chat import read_source_via_mcp

    calls = []

    class FakeMcp:
        def call_tool_sync(self, tool_use_id, name, arguments):
            calls.append((name, arguments))
            return {"structuredContent": {"status": "ok", "id": arguments["id"], "citation": "[KB:x]"}}

    out = read_source_via_mcp(FakeMcp(), "metric/defect_rate", None)
    assert out["status"] == "ok" and calls == [("read_source", {"id": "metric/defect_rate", "user_id": "anonymous"})]
