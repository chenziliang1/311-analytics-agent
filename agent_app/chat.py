# -*- coding: utf-8 -*-

"""
One chat turn, independent of where it runs.

The Vercel endpoint and the AgentCore container both serve the same turn: load
the browser's history into the agent, ask the last question, split the reply
into reasoning and answer. Keeping that here is what lets the web app run the
agent itself locally and hand the turn to AgentCore when deployed, without two
copies of the logic drifting apart.
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import io
import json
import threading
import typing as T
import uuid

from .agent_response import parse_agent_response
from .ai_sdk_adapter import get_last_user_message_text, request_body_to_agent_history
from .number_check import unverified_numbers
from .turn_observer import Listener, TurnObserver, summarize
from .vendor.vercel_ai_sdk import RequestBody

#: An empty answer streams an empty text block, and the UI then shows a
#: reasoning block with nothing under it, which reads as a hang. A visible
#: non-answer is debuggable; silence is not.
EMPTY_ANSWER = (
    "I worked through this but did not produce a final message. "
    "The reasoning above has what I found — ask me to restate it."
)


@dataclasses.dataclass(frozen=True)
class ChatResult:
    answer: str
    reasoning: str
    input_tokens: int = 0
    output_tokens: int = 0
    #: ok | clarify | error, from the turn's tool results (turn_observer.summarize).
    status: str = "ok"
    #: Each distinct warning the queries returned, for the bubble's "Note:" line.
    warnings: list[str] = dataclasses.field(default_factory=list)
    #: Results too long for a chat bubble, whole, for the CSV download.
    tables: list[dict] = dataclasses.field(default_factory=list)
    #: Numbers in the answer that no query this turn returned (number_check).
    unverified_numbers: list[str] = dataclasses.field(default_factory=list)

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ChatResult":
        return cls(
            answer=str(data.get("answer", "")),
            reasoning=str(data.get("reasoning", "")),
            input_tokens=int(data.get("input_tokens", 0) or 0),
            output_tokens=int(data.get("output_tokens", 0) or 0),
            status=str(data.get("status") or "ok"),
            warnings=[str(w) for w in data.get("warnings") or []],
            tables=list(data.get("tables") or []),
            unverified_numbers=[str(n) for n in data.get("unverified_numbers") or []],
        )


#: One agent instance serves every request in a process, and a turn replaces
#: its whole history. Two turns at once would interleave their histories, so
#: they queue. Concurrency comes from more processes (AgentCore microVMs,
#: Vercel instances), never from sharing one agent.
_AGENT_LOCK = threading.Lock()


def run_agent_turn(
    agent,
    request_body: RequestBody,
    on_status: Listener | None = None,
    user: str | None = None,
) -> ChatResult:
    """
    Run one turn on ``agent`` with the browser's history restored.

    The agent instance is long-lived, so its history is replaced, not appended
    to: the browser is the source of truth for the conversation.

    :param on_status: Called from this thread with each progress event (see
        turn_observer) while the turn runs.
    :param user: Who is asking, for the query audit log. None is anonymous
        (sign-in off).
    """
    with _AGENT_LOCK:
        observer = TurnObserver.of(agent)
        if observer is not None:
            observer.listener = on_status
            observer.identity = {
                "user_id": user or "anonymous",
                "session_id": request_body.id or "",
            }
        try:
            return _run_locked(agent, request_body, observer)
        finally:
            if observer is not None:
                observer.listener = None
                observer.identity = {}


def _run_locked(agent, request_body: RequestBody, observer: TurnObserver | None) -> ChatResult:
    agent.messages.clear()
    agent.messages.extend(request_body_to_agent_history(request_body))
    before = len(agent.messages)

    # Strands' default handler prints the stream to stdout; on Vercel stdout
    # is part of the response, so keep it out.
    with contextlib.redirect_stdout(io.StringIO()):
        result = agent(get_last_user_message_text(request_body))

    # NOT metrics.accumulated_usage: strands accumulates that across the
    # lifetime of the Agent instance, so it would re-count every earlier turn.
    # The per-invocation total is on the latest AgentInvocation.
    invocations = getattr(result.metrics, "agent_invocations", None) or []
    usage = invocations[-1].usage if invocations else {}

    reasoning, answer = parse_agent_response(agent.messages, before)
    results = observer.results if observer is not None else []
    summary = summarize(results)
    conversation = [part.text for message in request_body.messages for part in message.text_parts()]
    return ChatResult(
        answer=answer or EMPTY_ANSWER,
        reasoning=reasoning,
        input_tokens=int(usage.get("inputTokens", 0) or 0),
        output_tokens=int(usage.get("outputTokens", 0) or 0),
        unverified_numbers=unverified_numbers(answer, results, conversation),
        **summary,
    )


async def relay(run: T.Callable[[Listener], ChatResult]) -> T.AsyncIterator[tuple[str, T.Any]]:
    """
    Run a blocking turn in a worker thread and yield what it reports, live.

    ``run`` receives a listener to call with progress events. Yields
    ``("status", event)`` for each, then ``("result", ChatResult)``. An
    exception from ``run`` is raised here, after the events before it.
    """
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()

    def listener(event: dict) -> None:
        loop.call_soon_threadsafe(events.put_nowait, event)

    task = loop.run_in_executor(None, run, listener)
    while True:
        getter = asyncio.ensure_future(events.get())
        done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            yield "status", getter.result()
            continue
        getter.cancel()
        break
    # The turn is over; anything it queued in its last moments still counts.
    while not events.empty():
        yield "status", events.get_nowait()
    yield "result", task.result()


def runtime_session_id(chat_id: str | None) -> str:
    """
    An AgentCore session id for a browser chat: stable per chat, 33+ chars.

    The same chat keeps landing on the same microVM, so a follow-up does not
    pay a cold start; different chats never share one.
    """
    digest = hashlib.sha256((chat_id or "anonymous").encode("utf-8")).hexdigest()
    return f"chat-{digest[:40]}"


def _lines_as_they_arrive(stream) -> T.Iterator[bytes]:
    """
    The lines of a botocore response body, each as soon as it arrives.

    ``StreamingBody.iter_lines`` reads in 1 KB blocks and waits for each to
    fill, and a progress event is under 100 bytes: every one of them arrived
    together with the answer. ``read1`` on the underlying urllib3 response
    returns whatever has arrived instead.
    """
    raw = getattr(stream, "_raw_stream", None)
    if raw is None or not hasattr(raw, "read1"):
        yield from stream.iter_lines()
        return
    pending = b""
    while True:
        chunk = raw.read1(65536)
        if not chunk:
            break
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            yield line.rstrip(b"\r")
    if pending:
        yield pending


#: The payload field carrying the signed-in user to the AgentCore container.
USER_FIELD = "lens_user"


def invoke_remote_turn(
    client,
    runtime_arn: str,
    body: dict,
    on_status: Listener | None = None,
    user: str | None = None,
) -> ChatResult:
    """
    Hand the turn to the agent on AgentCore Runtime and read its reply.

    ``body`` is the browser's request body, forwarded unchanged: the container
    parses it exactly as the local path would, plus ``user`` as
    :data:`USER_FIELD` -- always overwritten here, so a browser that sends the
    field itself cannot claim to be someone else. The container streams progress
    events as Server-Sent Events and ends with the result; each event is
    passed to ``on_status`` as it arrives. A plain JSON reply (a container
    from before streaming) is read whole.
    """
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=runtime_session_id(body.get("id")),
        payload=json.dumps({**body, USER_FIELD: user}).encode("utf-8"),
        contentType="application/json",
        accept="text/event-stream",
    )
    stream = response["response"]
    if not str(response.get("contentType", "")).startswith("text/event-stream"):
        return ChatResult.from_json(json.loads(stream.read()))
    for line in _lines_as_they_arrive(stream):
        if not line.startswith(b"data: "):
            continue
        event = json.loads(line[len(b"data: "):])
        if event.get("type") == "status" and on_status is not None:
            on_status(event.get("data") or {})
        elif event.get("type") == "result":
            return ChatResult.from_json(event.get("data") or {})
        elif event.get("type") == "error":
            raise RuntimeError(event.get("message") or "the agent runtime failed")
    raise RuntimeError("the agent runtime closed the stream without a result")


# ------------------------------------------------------------------------------
# Feedback: thumbs up or down on an answer
# ------------------------------------------------------------------------------
#: The payload field that makes an AgentCore invocation a vote, not a turn.
FEEDBACK_FIELD = "lens_feedback"

#: What the browser may set on a vote. Anything else it sends is dropped;
#: the user always comes from the sign-in session.
FEEDBACK_KEYS = ("rating", "session_id", "message_id", "comment", "question", "answer")


def feedback_arguments(vote: dict, user: str | None) -> dict:
    """The record_feedback arguments: the browser's vote plus the signed-in user."""
    args = {k: str(vote[k]) for k in FEEDBACK_KEYS if vote.get(k) is not None}
    args["user_id"] = user or "anonymous"
    return args


def record_feedback_via_mcp(mcp_client, arguments: dict) -> dict:
    """
    Call the MCP server's record_feedback directly, not through the model.
    Returns its output: ``{"status": "ok", "feedback_id": ...}`` or an error.
    """
    from .mcp_server.feedback import FEEDBACK_TOOL
    from .turn_observer import tool_output

    result = mcp_client.call_tool_sync(
        tool_use_id=f"feedback-{uuid.uuid4()}", name=FEEDBACK_TOOL, arguments=arguments
    )
    return tool_output(result) or {"status": "error", "error": "no output from record_feedback"}


#: The payload field carrying a knowledge base read to the AgentCore container.
SOURCE_FIELD = "lens_source"


def read_source_via_mcp(mcp_client, source_id: str, user: str | None) -> dict:
    """
    Call the MCP server's read_source directly, not through the model: the
    browser showing a citation's document. Returns the tool's output.
    """
    from .turn_observer import tool_output

    result = mcp_client.call_tool_sync(
        tool_use_id=f"source-{uuid.uuid4()}",
        name="read_source",
        arguments={"id": source_id, "user_id": user or "anonymous"},
    )
    return tool_output(result) or {"status": "error", "id": source_id, "warning": "no output from read_source"}


def invoke_remote_source(client, runtime_arn: str, source_id: str, chat_id: str | None, user: str | None) -> dict:
    """Ask the agent runtime for a document, through the chat's own warm session."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=runtime_session_id(chat_id),
        payload=json.dumps({SOURCE_FIELD: {"id": source_id}, USER_FIELD: user}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["response"].read())


def invoke_remote_feedback(client, runtime_arn: str, vote: dict, user: str | None) -> dict:
    """Hand a vote to the agent runtime, which records it through its MCP client."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        # The chat's own session: its microVM is warm and already connected.
        runtimeSessionId=runtime_session_id(vote.get("session_id")),
        payload=json.dumps({FEEDBACK_FIELD: vote, USER_FIELD: user}).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["response"].read())
