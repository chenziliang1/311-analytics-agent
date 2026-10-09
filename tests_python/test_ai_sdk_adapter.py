# -*- coding: utf-8 -*-

"""
Tests for the frontend/agent translation layer.

Everything here is offline: the module is pure translation, and the protocol it
speaks is exactly the kind of contract that breaks silently -- a wrong event
type does not raise, it just leaves the browser spinning forever.
"""

import json
import asyncio

from agent_app.ai_sdk_adapter import (
    REDACTED,
    SSE_HEADERS,
    ai_sdk_message_with_reasoning_generator,
    ai_sdk_streaming_response,
    get_last_user_message_text,
    read_request_body,
    request_body_to_agent_history,
)
from agent_app.vendor.vercel_ai_sdk import RequestBody


def body(*messages) -> RequestBody:
    """Build a RequestBody the way the frontend actually posts one."""
    return RequestBody(id="c1", trigger="submit-message", messages=list(messages))


def msg(role: str, *parts) -> dict:
    return {"id": f"m-{role}", "role": role, "parts": list(parts)}


def text(s: str) -> dict:
    return {"type": "text", "text": s}


def reasoning(s: str) -> dict:
    return {"type": "reasoning", "text": s}


def events(chunks) -> list[dict]:
    """Parse the SSE stream back into payloads, dropping the [DONE] marker."""
    out = []
    for chunk in chunks:
        assert chunk.endswith("\n\n"), "every SSE event ends with a blank line"
        payload = chunk[len("data: ") : -2]
        if payload != "[DONE]":
            out.append(json.loads(payload))
    return out


class TestGetLastUserMessageText:
    def test_takes_the_last_message(self):
        b = body(msg("user", text("first")), msg("assistant", text("reply")),
                 msg("user", text("second")))
        assert get_last_user_message_text(b) == "second"

    def test_joins_several_text_parts(self):
        assert get_last_user_message_text(body(msg("user", text("a"), text("b")))) == "ab"

    def test_reasoning_is_not_mistaken_for_what_the_user_said(self):
        b = body(msg("user", reasoning("thinking out loud"), text("the question")))
        assert get_last_user_message_text(b) == "the question"

    def test_empty_request_returns_none(self):
        """A client bug deserves a readable message, not a 500."""
        assert get_last_user_message_text(body()) is None

    def test_message_with_no_text_returns_none(self):
        assert get_last_user_message_text(body(msg("user", reasoning("only this")))) is None


class TestRequestBodyToAgentHistory:
    def test_the_last_message_is_excluded(self):
        """It is the question being asked now, so it is input, not history."""
        b = body(msg("user", text("q1")), msg("assistant", text("a1")),
                 msg("user", text("q2")))
        history = request_body_to_agent_history(b)
        assert history == [
            {"role": "user", "content": [{"text": "q1"}]},
            {"role": "assistant", "content": [{"text": "a1"}]},
        ]

    def test_reasoning_is_dropped(self):
        """Those blocks are long, and would be resent and paid for every turn."""
        b = body(msg("assistant", reasoning("long thinking"), text("answer")),
                 msg("user", text("next")))
        assert request_body_to_agent_history(b) == [
            {"role": "assistant", "content": [{"text": "answer"}]}
        ]

    def test_a_message_with_no_text_is_skipped(self):
        """Providers reject empty content, so it must not be sent at all."""
        b = body(msg("assistant", reasoning("only thinking")), msg("user", text("next")))
        assert request_body_to_agent_history(b) == []

    def test_a_single_message_yields_no_history(self):
        assert request_body_to_agent_history(body(msg("user", text("hi")))) == []


class TestSseStream:
    def test_event_order(self):
        parsed = events(ai_sdk_message_with_reasoning_generator("thinking", "answer"))
        assert [e["type"] for e in parsed] == [
            "reasoning-start", "reasoning-delta", "reasoning-end",
            "text-start", "text-delta", "text-end",
            "finish",
        ]

    def test_empty_reasoning_skips_its_block(self):
        """An empty block renders as a toggle with nothing inside it."""
        parsed = events(ai_sdk_message_with_reasoning_generator("", "answer"))
        assert [e["type"] for e in parsed] == [
            "text-start", "text-delta", "text-end", "finish"
        ]

    def test_deltas_carry_the_content(self):
        parsed = events(ai_sdk_message_with_reasoning_generator("why", "what"))
        by_type = {e["type"]: e for e in parsed}
        assert by_type["reasoning-delta"]["delta"] == "why"
        assert by_type["text-delta"]["delta"] == "what"

    def test_ids_group_a_block_and_differ_between_blocks(self):
        """The frontend matches every delta to its block by id."""
        parsed = events(ai_sdk_message_with_reasoning_generator("why", "what"))
        by_type = {e["type"]: e for e in parsed}
        r_ids = {by_type[f"reasoning-{p}"]["id"] for p in ("start", "delta", "end")}
        t_ids = {by_type[f"text-{p}"]["id"] for p in ("start", "delta", "end")}
        assert len(r_ids) == 1 and len(t_ids) == 1
        assert r_ids != t_ids

    def test_stream_ends_with_finish_then_done(self):
        """Without the finish event the UI spins forever."""
        chunks = list(ai_sdk_message_with_reasoning_generator("", "answer"))
        assert json.loads(chunks[-2][len("data: ") : -2])["type"] == "finish"
        assert chunks[-1] == "data: [DONE]\n\n"

    def test_non_ascii_survives_the_json_round_trip(self):
        parsed = events(ai_sdk_message_with_reasoning_generator("", "净利润 90%"))
        assert parsed[1]["delta"] == "净利润 90%"


class TestStreamingResponse:
    def test_carries_the_protocol_headers(self):
        """
        The AI SDK needs x-vercel-ai-ui-message-stream to parse the body as a v5
        stream at all; without it the frontend silently renders nothing.
        """
        response = ai_sdk_streaming_response("answer")
        for key, value in SSE_HEADERS.items():
            assert response.headers[key] == value
        assert response.media_type == "text/event-stream"

    def test_reasoning_is_optional(self):
        assert ai_sdk_streaming_response("answer").status_code == 200
        assert ai_sdk_streaming_response("answer", reasoning_text="why").status_code == 200


class StubRequest:
    """Enough of a FastAPI Request for the logger: headers, and an awaitable body."""

    def __init__(self, headers, payload):
        self.headers = headers
        self._payload = payload

    async def json(self):
        return self._payload


class TestRequestLogging:
    def test_credentials_are_not_written_to_the_log(self, capsys):
        """
        These logs are retained by the host, so anything that authenticates a
        caller must never reach them.
        """
        request = StubRequest(
            headers={
                "authorization": "Bearer sk-secret",
                "cookie": "session=abc",
                "content-type": "application/json",
            },
            payload={"id": "c1", "messages": []},
        )
        result = asyncio.run(read_request_body(request))

        err = capsys.readouterr().err
        assert "sk-secret" not in err
        assert "session=abc" not in err
        assert err.count(REDACTED) == 2
        # Non-sensitive headers still come through, or the log is useless.
        assert "application/json" in err
        # Returns the body too, because a request can only be read once.
        assert result == {"id": "c1", "messages": []}

    def test_case_does_not_matter(self, capsys):
        """HTTP header names are case-insensitive; a client may send Authorization."""
        request = StubRequest(
            headers={"Authorization": "Bearer sk-secret"}, payload={"messages": []}
        )
        asyncio.run(read_request_body(request))
        assert "sk-secret" not in capsys.readouterr().err
