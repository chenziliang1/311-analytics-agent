# -*- coding: utf-8 -*-

"""
FastAPI backend for AI chat powered by Strands Agent.

This module provides the API endpoints that connect the frontend chat UI
to the Strands Agent with write operation capabilities. It implements the
Vercel AI SDK v5 Data Stream Protocol using Server-Sent Events (SSE).

Key components:
- /api/hello: Health check endpoint
- /api/chat: Main chat endpoint that processes messages and returns AI responses
  with both reasoning (thinking) and text content
"""

import os
import sys

# fmt: off
from fastapi import FastAPI, Request, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from agent_app.vendor.vercel_ai_sdk import RequestBody  # Parses AI SDK request format
from agent_app.utils import debug
from agent_app.ai_sdk_adapter import ai_sdk_error_events
from agent_app.ai_sdk_adapter import ai_sdk_event_stream_response
from agent_app.ai_sdk_adapter import ai_sdk_message_with_reasoning_generator
from agent_app.ai_sdk_adapter import ai_sdk_status_event
from agent_app.ai_sdk_adapter import ai_sdk_streaming_response
from agent_app.ai_sdk_adapter import get_last_user_message_text
from agent_app.ai_sdk_adapter import read_request_body
from agent_app.ai_sdk_adapter import turn_data_parts
from agent_app.one.api import one  # Main singleton with agent
from agent_app.chat import feedback_arguments
from agent_app.chat import invoke_remote_feedback
from agent_app.chat import invoke_remote_source
from agent_app.chat import read_source_via_mcp
from agent_app.chat import invoke_remote_turn
from agent_app.chat import record_feedback_via_mcp
from agent_app.chat import relay
from agent_app.chat import run_agent_turn
from agent_app.quota import check_quota
from agent_app.quota import increment_usage
from agent_app.quota import QuotaExceededError
# fmt: on

# Add project root to sys.path for module imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

app = FastAPI()

#: Shown when the monthly cap is reached. A constant, not a model call: the
#: whole point is that this path spends nothing.
QUOTA_EXCEEDED_MESSAGE = (
    "This demo runs on a monthly token budget, and it has been used up for this "
    "billing cycle. The chat will start working again when the budget resets at "
    "the beginning of next month. Thanks for trying it out."
)


@app.get("/api/hello")
async def hello_world():
    """
    Health check endpoint for testing FastAPI integration.

    Returns a simple JSON response to verify the API is running.
    """
    return JSONResponse(
        content={
            "message": "Hello from FastAPI!",
            "status": "success",
        },
    )


@app.post("/api/feedback")
async def handle_feedback(request: Request):
    """
    A thumbs up or down on one answer, with an optional comment.

    Stored in the warehouse's FEEDBACK table by the MCP server, which holds
    the credentials: locally through the agent's own MCP connection,
    deployed through the agent runtime. The user is the signed-in one.
    """
    try:
        vote = await request.json()
    except Exception:
        return JSONResponse({"status": "invalid", "error": "not JSON"}, status_code=400)
    if not isinstance(vote, dict) or vote.get("rating") not in ("up", "down"):
        return JSONResponse({"status": "invalid", "error": "rating must be up or down"}, status_code=400)
    user = request.headers.get("x-lens-user") or None

    runtime_arn = one.config.agentcore_runtime_arn
    try:
        if runtime_arn:
            output = await run_in_threadpool(
                invoke_remote_feedback, one.bedrock_agentcore_client(), runtime_arn, vote, user
            )
        else:

            def record():
                _ = one.agent  # building the agent starts its MCP client
                return record_feedback_via_mcp(one.mcp_client, feedback_arguments(vote, user))

            output = await run_in_threadpool(record)
    except Exception as e:
        debug(f"[Feedback] failed: {type(e).__name__}: {e}")
        return JSONResponse({"status": "error", "error": type(e).__name__}, status_code=502)
    debug(f"[Feedback] {vote.get('rating')} from {user or 'anonymous'}: {output.get('status')}")
    return JSONResponse(output, status_code=200 if output.get("status") == "ok" else 400)


@app.get("/api/source/{source_id:path}")
async def handle_source(source_id: str, request: Request, chat: str | None = None):
    """
    One knowledge base document, for a citation the user opened in the chat.

    Read by the MCP server as the signed-in user, the same way the agent reads
    it: locally through the agent's own MCP connection, deployed through the
    agent runtime, on the chat's own session so the microVM is already warm.
    """
    user = request.headers.get("x-lens-user") or None
    runtime_arn = one.config.agentcore_runtime_arn
    try:
        if runtime_arn:
            output = await run_in_threadpool(
                invoke_remote_source, one.bedrock_agentcore_client(), runtime_arn, source_id, chat, user
            )
        else:

            def read():
                _ = one.agent  # building the agent starts its MCP client
                return read_source_via_mcp(one.mcp_client, source_id, user)

            output = await run_in_threadpool(read)
    except Exception as e:
        debug(f"[Source] {source_id} failed: {type(e).__name__}: {e}")
        return JSONResponse({"status": "error", "id": source_id, "warning": type(e).__name__}, status_code=502)
    status = {"ok": 200, "not_found": 404, "blocked": 403}.get(str(output.get("status")), 502)
    return JSONResponse(output, status_code=status)


@app.post("/api/chat")
async def handle_chat_data(request: Request, protocol: str = Query("data")):
    """
    Main chat endpoint that processes user messages and returns AI responses.

    This endpoint uses the Strands Agent to process messages and returns
    responses with both reasoning (thinking) and text content using the
    Vercel AI SDK v5 Data Stream Protocol.

    The agent's data tools live on the MCP server (warehouse_query,
    visualize). With AGENTCORE_RUNTIME_ARN set, the whole turn runs on
    AgentCore Runtime instead of in this process.

    Args:
        request: The incoming HTTP request containing chat messages
        protocol: Stream protocol version (default: "data" for AI SDK v5)
    """
    # --- Who is asking: set by proxy.ts from the sign-in session, if on ---
    # Recorded with every query this turn runs (turn_observer).
    user = request.headers.get("x-lens-user") or None

    # --- Log incoming request for troubleshooting
    request_body_data = await read_request_body(request=request)

    # --- Parse the incoming request into AI SDK format
    request_body = RequestBody(**request_body_data)

    # --- Extract the last user message ---
    last_user_message = get_last_user_message_text(request_body)

    if not last_user_message:
        return ai_sdk_streaming_response("Error: No message content found in request.")

    # --- Refuse before spending, if this deployment caps its monthly spend ---
    # A no-op when the cap is off, and it swallows its own infrastructure
    # faults, so the only thing that reaches here is an actual refusal.
    try:
        check_quota(one.dynamodb_client(), one.config)
    except QuotaExceededError as e:
        debug(f"[Quota] blocked: {e}")
        return ai_sdk_streaming_response(QUOTA_EXCEEDED_MESSAGE)

    # --- Run the turn: here, or on AgentCore Runtime when one is configured ---
    # Same request body either way; the container runs the same run_agent_turn.
    # Both block for the whole turn, so it runs in a worker thread while its
    # progress ("querying the warehouse") streams to the browser.
    runtime_arn = one.config.agentcore_runtime_arn
    if runtime_arn:
        client = one.bedrock_agentcore_client()

        def run(listener):
            return invoke_remote_turn(client, runtime_arn, request_body_data, listener, user)
    else:

        def run(listener):
            return run_agent_turn(one.agent, request_body, listener, user)

    async def events():
        result = None
        try:
            async for kind, value in relay(run):
                if kind == "status":
                    yield ai_sdk_status_event(value)
                else:
                    result = value
        except Exception as e:
            debug(f"[Agent] turn failed: {type(e).__name__}: {e}")
            for chunk in ai_sdk_error_events(f"The agent failed: {type(e).__name__}"):
                yield chunk
            return

        # --- Meter what that cost ---
        increment_usage(
            one.dynamodb_client(),
            one.config,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        debug(
            f"[Agent] {'remote' if runtime_arn else 'local'} turn for {user or 'anonymous'}: "
            f"{len(result.answer)} chars, {result.status}"
        )

        # --- The answer, its reasoning, and what the UI needs besides ---
        for chunk in ai_sdk_message_with_reasoning_generator(
            reasoning_text=result.reasoning,
            output_text=result.answer,
            data_parts=turn_data_parts(
                result.status, result.warnings, result.tables, result.unverified_numbers, result.values
            ),
        ):
            yield chunk

    return ai_sdk_event_stream_response(events())
