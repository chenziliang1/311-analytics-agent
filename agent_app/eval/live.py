# -*- coding: utf-8 -*-

"""
The real pieces the runner is handed: the chat endpoint, the database, the judge.

Kept apart from ``runner.py`` so that everything scoring-related is testable
without a model, a database, or a web app.
"""

import json
import time

from .runner import Turn


def _sse_text(body: str) -> str:
    """The answer text out of an AI SDK data stream."""
    parts = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "text-delta":
            parts.append(event.get("delta", ""))
    return "".join(parts)


def _tool_payload(block: dict) -> dict | None:
    for part in block.get("content", []):
        if isinstance(part.get("json"), dict):
            return part["json"]
        if "text" in part:
            try:
                data = json.loads(part["text"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return data
    return None


def tool_activity(messages: list[dict]) -> tuple[list[tuple[str, dict]], list[dict]]:
    """
    Tool calls, and the parsed output of every warehouse_query call, from the
    agent's message list. Outputs are paired to calls by tool use id.
    """
    calls, names = [], {}
    outputs = []
    for message in messages:
        for block in message.get("content", []):
            if "toolUse" in block:
                use = block["toolUse"]
                calls.append((use["name"], use.get("input", {})))
                names[use["toolUseId"]] = use["name"]
            if "toolResult" in block:
                result = block["toolResult"]
                if names.get(result.get("toolUseId")) == "warehouse_query":
                    payload = _tool_payload(result)
                    if payload is not None:
                        outputs.append(payload)
    return calls, outputs


def app_chat():  # pragma: no cover - drives the real agent
    """A ``Chat`` that posts to ``/api/chat`` in process, as the browser does."""
    import sys

    from fastapi.testclient import TestClient

    from ..api import one
    from ..paths import path_enum

    # The web app lives in the repo's top-level api/, outside the package, so
    # it is only importable with the repo root on the path. A console script
    # does not put it there.
    root = str(path_enum.dir_project_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    from api.index import app

    client = TestClient(app)

    def chat(conversation: list[tuple[str, str]]) -> Turn:
        messages = [
            {"id": f"m{i}", "role": role, "parts": [{"type": "text", "text": text}]}
            for i, (role, text) in enumerate(conversation)
        ]
        started = time.monotonic()
        # Named, so the audit log tells graded queries from real ones.
        response = client.post(
            "/api/chat",
            json={"id": "eval", "messages": messages},
            headers={"x-lens-user": "golden-set-eval"},
        )
        seconds = time.monotonic() - started
        # The endpoint resets the agent's history to the request's, so what is
        # left in it now is exactly this turn's tool activity plus that history.
        calls, outputs = tool_activity(one.agent.messages)
        return Turn(
            answer=_sse_text(response.text),
            seconds=seconds,
            tool_calls=calls,
            query_outputs=outputs,
        )

    return chat


def db_reference():  # pragma: no cover - needs the database
    from ..api import one
    from ..sql_guard import transpile_sql
    from ..sql_utils import fetch_rows

    def reference(sql: str) -> tuple[list[str], list[list]]:
        # References are PostgreSQL; on Snowflake they get the same
        # translation as the agent's queries, so a translation bug cannot
        # make the agent look right or wrong on its own.
        result = fetch_rows(
            one.engine, transpile_sql(sql, one.sql_dialect), max_rows=500, timeout_seconds=120
        )
        if result.error:
            raise RuntimeError(f"reference query failed: {result.error}")
        return result.columns, result.rows

    return reference


def model_judge():  # pragma: no cover - calls a model
    from strands import Agent

    from ..api import one
    from .judge import JUDGE_SYSTEM_PROMPT

    config = one.config
    same_as_writer = (config.eval_judge_provider, config.eval_judge_model_id) == (
        config.sql_writer_provider,
        config.sql_writer_model_id,
    )
    model = one.new_model(
        provider=config.eval_judge_provider,
        model_id=config.eval_judge_model_id,
        # The writer's setting already knows whether this model takes a
        # temperature; any other judge gets the reproducible 0.
        temperature=config.sql_writer_temperature if same_as_writer else 0.0,
        cache_conversation=False,
    )

    def judge(request: str) -> str:
        agent = Agent(model=model, system_prompt=JUDGE_SYSTEM_PROMPT, tools=[], callback_handler=None)
        return str(agent(request))

    return judge
