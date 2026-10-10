# -*- coding: utf-8 -*-

"""
The agent as an AgentCore Runtime container (HTTP protocol).

AgentCore's contract: listen on 0.0.0.0:8080, answer GET /ping with a status,
and serve POST /invocations. The payload here is the browser's AI SDK request
body, forwarded unchanged by the web app, and the reply is a ChatResult as
JSON; the web app turns it into the SSE stream the browser reads.
"""

import contextlib
import json
import threading
import time

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from .ai_sdk_adapter import get_last_user_message_text
from .chat import (
    FEEDBACK_FIELD,
    USER_FIELD,
    ChatResult,
    feedback_arguments,
    record_feedback_via_mcp,
    relay,
    run_agent_turn,
)
from .vendor.vercel_ai_sdk import RequestBody


def build_app(agent_factory=None, metrics=None, feedback_recorder=None) -> FastAPI:
    """
    :param agent_factory: Returns the agent; the default is ``one.agent``,
        built in the background at startup so /ping answers right away.
    :param metrics: Where turn metrics go; the default follows the config.
    :param feedback_recorder: Records one vote's record_feedback arguments and
        returns the tool's output; the default calls the MCP server directly.
    """
    state = {"turns": 0}
    building = threading.Lock()

    def agent():
        if agent_factory is not None:
            return agent_factory()
        from .api import one  # pragma: no cover - the real agent

        # The warm-up thread and the first request can both get here while
        # the agent is being built; it should be built once.
        with building:  # pragma: no cover
            return one.agent

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if agent_factory is None:  # pragma: no cover - builds the real agent
            # Build the agent and open its MCP connection now, not on the
            # first question: that also starts the tools runtime's own cold
            # start while nobody is waiting yet. /ping answers meanwhile.
            threading.Thread(target=agent, name="agent-warm-up", daemon=True).start()
        yield

    app = FastAPI(lifespan=lifespan)

    def sink():
        if metrics is not None:
            return metrics
        from .api import one  # pragma: no cover

        if "metrics" not in state:  # pragma: no cover
            state["metrics"] = one.metrics("agent")
        return state["metrics"]  # pragma: no cover

    @app.get("/ping")
    async def ping():
        # No time_of_last_update: a timestamp that moves on every ping keeps
        # the session from ever idling out.
        return {"status": "Healthy"}

    def record_vote(arguments: dict) -> dict:
        if feedback_recorder is not None:
            return feedback_recorder(arguments)
        from .api import one  # pragma: no cover - the real MCP connection

        agent()  # pragma: no cover - building the agent starts the MCP client
        return record_feedback_via_mcp(one.mcp_client, arguments)  # pragma: no cover

    @app.post("/invocations")
    async def invocations(request: Request):
        try:
            data = await request.json()
        except Exception as e:
            return JSONResponse({"error": f"not JSON: {e}"}, status_code=400)
        if isinstance(data, dict) and isinstance(data.get(FEEDBACK_FIELD), dict):
            # A thumbs up or down, not a question.
            arguments = feedback_arguments(data[FEEDBACK_FIELD], data.get(USER_FIELD))
            output = await run_in_threadpool(record_vote, arguments)
            return JSONResponse(output, status_code=200 if output.get("status") == "ok" else 400)
        try:
            body = RequestBody(**data)
        except Exception as e:
            return JSONResponse({"error": f"not an AI SDK request body: {e}"}, status_code=400)
        if not get_last_user_message_text(body):
            return JSONResponse({"error": "no user message in the request"}, status_code=400)
        # Set by the web app from the sign-in session (chat.invoke_remote_turn);
        # only a caller allowed to invoke this runtime can send it.
        user = data.get(USER_FIELD) if isinstance(data, dict) else None

        def record(result: ChatResult, seconds: float) -> None:
            m = sink()
            if state["turns"] == 0:
                # The first turn in a microVM pays for building the agent and its
                # MCP connection: that is the cold start the dashboard shows.
                m.put("ColdStartTurns", 1)
            state["turns"] += 1
            m.put("Turns", 1)
            m.put("TurnLatency", seconds, unit="Seconds")
            m.put("InputTokens", result.input_tokens)
            m.put("OutputTokens", result.output_tokens)
            # Turns with a number no query returned: most are derived ratios,
            # so the level means little; a jump after a model or prompt change
            # is the signal.
            m.put("UnverifiedNumberTurns", 1 if result.unverified_numbers else 0)

        started = time.monotonic()
        if "text/event-stream" not in request.headers.get("accept", ""):
            # agent() too, off the event loop: it can wait on the warm-up.
            result = await run_in_threadpool(lambda: run_agent_turn(agent(), body, user=user))
            record(result, time.monotonic() - started)
            return JSONResponse(result.to_json())

        async def events():
            # Progress as it happens, then the result: the web app relays the
            # progress to the browser while the turn is still running.
            try:
                async for kind, value in relay(lambda listener: run_agent_turn(agent(), body, listener, user)):
                    if kind == "status":
                        yield _sse({"type": "status", "data": value})
                    else:
                        record(value, time.monotonic() - started)
                        yield _sse({"type": "result", "data": value.to_json()})
            except Exception as e:
                yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"
