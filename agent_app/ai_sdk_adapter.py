# -*- coding: utf-8 -*-

"""
Translation layer between the Vercel AI SDK frontend and the Strands Agent.

The only place that knows both dialects: the frontend's typed message *parts*
going in, the AI SDK v5 Data Stream Protocol going out.

**Nothing here is provider-specific, and it must stay that way.** The message
shape the agent uses is the one AWS invented for Bedrock Converse, and Strands
kept it across every provider, so switching ``LLM_PROVIDER`` does not touch this
file. Provider-specific code belongs next to the model construction in
``one_04_agent.py``.

The request data model itself is vendored in ``vendor/vercel_ai_sdk.py``.
"""

import sys
import json
import uuid
import typing as T

from fastapi import Request
from fastapi.responses import StreamingResponse

from .utils import debug
from .vendor.vercel_ai_sdk import RequestBody


# ------------------------------------------------------------------------------
# Inbound: frontend JSON -> agent input
# ------------------------------------------------------------------------------
def get_last_user_message_text(request_body: RequestBody) -> str | None:
    """
    The text of the newest user message -- the question to answer now.

    Returns None rather than raising on an empty request: that is a client bug,
    and the useful response to it is a message, not a stack trace.
    """
    if not request_body.messages:
        return None

    # Only the visible-text parts, so a reasoning block is never mistaken for
    # what the user said.
    text = "".join(part.text for part in request_body.messages[-1].text_parts())
    return text or None


def request_body_to_agent_history(request_body: RequestBody) -> list[dict]:
    """
    Convert the conversation so far into the agent's message format.

    The last message is excluded: that is the question being asked now, which
    goes to the agent as input rather than as history.

    Reasoning parts are dropped -- the agent gains nothing from re-reading its
    own thinking, and those blocks would be resent, and paid for, every turn.
    A message left with no text is skipped, since providers reject empty content.
    """
    messages = []

    for message in request_body.messages[:-1]:
        content = [{"text": part.text} for part in message.text_parts()]
        if content:
            messages.append({"role": message.role, "content": content})

    return messages


# ------------------------------------------------------------------------------
# Outbound: agent output -> AI SDK Data Stream Protocol (SSE)
# ------------------------------------------------------------------------------

#: Required by the AI SDK to parse the body as a v5 message stream, plus the two
#: that stop a proxy or browser from buffering it. Part of the protocol, so they
#: travel with the generator rather than being spelled out at each call site.
SSE_HEADERS = {
    "x-vercel-ai-ui-message-stream": "v1",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}


def _sse(**payload) -> str:
    """Format one Server-Sent Event line from a JSON payload."""
    # default=str: query rows can hold dates and decimals.
    return f"data: {json.dumps(payload, default=str)}\n\n"


def ai_sdk_message_with_reasoning_generator(
    reasoning_text: str,
    output_text: str,
    data_parts: T.Sequence[tuple[str, dict]] = (),
) -> T.Iterator[str]:
    """
    Stream a finished answer, with its thinking, as AI SDK v5 SSE events.

    Each block is start / delta / end so the UI can open, fill and close it, and
    carries an id so concurrent blocks can be told apart. One delta holds the
    whole string because the agent has already finished; real token streaming
    would emit many deltas and change nothing else.

    An empty ``reasoning_text`` skips those events entirely -- an empty block
    renders as a toggle with nothing inside it.

    :param data_parts: ``(name, data)`` pairs sent after the text as
        ``data-<name>`` parts, which the UI keeps on the message (the turn's
        status and warnings, a table to download).
    """
    if reasoning_text:
        reasoning_id = str(uuid.uuid4())
        yield _sse(type="reasoning-start", id=reasoning_id)
        yield _sse(type="reasoning-delta", id=reasoning_id, delta=reasoning_text)
        yield _sse(type="reasoning-end", id=reasoning_id)

    text_id = str(uuid.uuid4())
    yield _sse(type="text-start", id=text_id)
    yield _sse(type="text-delta", id=text_id, delta=output_text)
    yield _sse(type="text-end", id=text_id)

    for name, data in data_parts:
        yield _sse(type=f"data-{name}", id=str(uuid.uuid4()), data=data)

    # Without this the UI keeps showing a spinner forever.
    yield _sse(type="finish", finishReason="stop")

    # Stream termination marker, defined by the protocol rather than by us.
    yield "data: [DONE]\n\n"


def ai_sdk_status_event(event: dict) -> str:
    """
    One progress event (see turn_observer) as a transient ``data-status``
    part: the UI shows it while the turn runs, and it is never added to the
    message, so it is not sent back with the next request.
    """
    return _sse(type="data-status", data=event, transient=True)


def ai_sdk_error_events(error_text: str) -> T.Iterator[str]:
    """A failed turn: the UI's error state, then the end of the stream."""
    yield _sse(type="error", errorText=error_text)
    yield "data: [DONE]\n\n"


def turn_data_parts(
    status: str,
    warnings: T.Sequence[str],
    tables: T.Sequence[dict],
    unverified: T.Sequence[str] = (),
) -> list[tuple[str, dict]]:
    """
    The data parts a finished turn carries: always one ``turn`` (status,
    warnings, and the numbers no query returned), plus one ``table`` per
    result offered as a CSV.
    """
    parts = [("turn", {"status": status, "warnings": list(warnings), "unverified": list(unverified)})]
    parts.extend(("table", table) for table in tables)
    return parts


def ai_sdk_streaming_response(
    output_text: str,
    reasoning_text: str = "",
) -> StreamingResponse:
    """
    A ready-to-return SSE response carrying one finished answer.

    Used for the replies that are not an agent turn -- an empty request, a
    spent quota -- so the headers live here instead of being rebuilt at each
    call site.
    """
    return ai_sdk_event_stream_response(
        ai_sdk_message_with_reasoning_generator(
            reasoning_text=reasoning_text,
            output_text=output_text,
        )
    )


def ai_sdk_event_stream_response(events: T.Iterator[str] | T.AsyncIterator[str]) -> StreamingResponse:
    """Any stream of the SSE events above, with the protocol's headers."""
    response = StreamingResponse(events, media_type="text/event-stream")
    response.headers.update(SSE_HEADERS)
    return response


# ------------------------------------------------------------------------------
# Request logging
# ------------------------------------------------------------------------------

#: Headers replaced with a placeholder before logging. These logs are retained
#: by the host, so anything that authenticates a caller must not reach them.
SENSITIVE_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}
)

REDACTED = "<redacted>"


async def read_request_body(request: Request) -> dict:
    """
    Read the JSON body, logging the request on the way through.

    Logs *and* returns because a request body can only be read once: reading it
    here and throwing it away would leave the caller with nothing to parse.
    """
    debug("====== Incoming request")
    debug("------ Request Headers")
    for key, value in request.headers.items():
        shown = REDACTED if key.lower() in SENSITIVE_HEADERS else value
        debug(f"{key}: {shown}")
    debug("------ Request Body")
    request_body_data = await request.json()
    debug(json.dumps(request_body_data, indent=2, ensure_ascii=False))
    sys.stderr.flush()
    return request_body_data
