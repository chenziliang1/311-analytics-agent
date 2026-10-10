# -*- coding: utf-8 -*-

"""
What happened inside one agent turn, for the UI.

Two things the chat needs that the answer text does not carry:

- **Progress while the turn runs.** A turn is 5 to 20 seconds of silence
  otherwise. Each model call and tool call is reported to a listener as it
  starts, and the web app streams those to the browser ("Querying the
  warehouse...").
- **Who is asking, on every query.** The signed-in user and the chat id are
  written into each warehouse_query call's arguments here, after the model
  has produced them, so the audit log records them and the model can neither
  omit nor change them.
- **The tool results behind the answer.** The agent's prose shows the top of
  a result; the UI offers the full rows as a CSV, shows the tool's warnings,
  and turns a failed query into an error banner. Those come from the
  warehouse_query outputs collected here, not from parsing the prose.

Registered on the agent as a hook provider. One agent serves one turn at a
time (see ``chat._AGENT_LOCK``), so per-turn state on the observer is safe.
"""

import json
import typing as T
import weakref

from strands.hooks import (
    AfterToolCallEvent,
    AgentInitializedEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

#: Receives one progress event: ``{"phase": ..., "detail": ...}``.
Listener = T.Callable[[dict], None]

#: A result with more rows than this is offered as a CSV download: past 20
#: rows a chat bubble stops being readable.
CSV_MIN_ROWS = 20

#: The tools whose calls carry the asker's identity: for the audit log, and
#: for the warehouse role their reads run as.
IDENTIFIED_TOOLS = frozenset({"warehouse_query", "list_sources", "read_source"})

#: Query statuses that mean the tool could not produce an answer.
FAILED_STATUSES = frozenset({"sql_error", "timeout"})

_OBSERVERS: "weakref.WeakKeyDictionary[T.Any, TurnObserver]" = weakref.WeakKeyDictionary()


class TurnObserver(HookProvider):
    def __init__(self):
        self.listener: Listener | None = None
        #: ``user_id`` and ``session_id`` for this turn, set by run_agent_turn.
        self.identity: dict[str, str] = {}
        #: (tool name, parsed output) for every tool call this turn.
        self.results: list[tuple[str, dict]] = []
        self._tool_calls = 0

    @staticmethod
    def of(agent) -> "TurnObserver | None":
        """The observer registered on ``agent``, if it has one."""
        try:
            return _OBSERVERS.get(agent)
        except TypeError:  # pragma: no cover - an agent that cannot be weakly referenced
            return None

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(AgentInitializedEvent, self._attach)
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeModelCallEvent, self._model_call)
        registry.add_callback(BeforeToolCallEvent, self._tool_call)
        registry.add_callback(AfterToolCallEvent, self._tool_result)

    def _attach(self, event: AgentInitializedEvent) -> None:
        _OBSERVERS[event.agent] = self

    def _reset(self, event: BeforeInvocationEvent) -> None:
        self.results = []
        self._tool_calls = 0

    def _emit(self, phase: str, detail: str | None = None) -> None:
        if self.listener is None:
            return
        try:
            self.listener({"phase": phase, "detail": detail})
        except Exception:  # pragma: no cover - a broken UI stream must not end the turn
            pass

    def _model_call(self, event: BeforeModelCallEvent) -> None:
        # Before any tool the model is deciding what to do; after one, it is
        # writing the answer from the results.
        self._emit("answering" if self._tool_calls else "thinking")

    def _tool_call(self, event: BeforeToolCallEvent) -> None:
        self._tool_calls += 1
        name = event.tool_use.get("name")
        if name in IDENTIFIED_TOOLS and self.identity:
            # Overwrites whatever the model put there.
            event.tool_use["input"] = {**(event.tool_use.get("input") or {}), **self.identity}
        tool_input = event.tool_use.get("input") or {}
        detail = tool_input.get("intent") if isinstance(tool_input, dict) else None
        self._emit(event.tool_use.get("name") or "tool", detail)

    def _tool_result(self, event: AfterToolCallEvent) -> None:
        output = tool_output(event.result)
        if output is not None:
            self.results.append((event.tool_use.get("name") or "", output))


def tool_output(result: T.Any) -> dict | None:
    """
    The structured output inside a strands tool result, or None.

    An MCP tool that returns a model arrives as ``structuredContent``, and as
    the same JSON in a text block; FastMCP wraps a non-object in
    ``{"result": ...}``. Either is accepted.
    """
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def summarize(results: T.Sequence[tuple[str, dict]]) -> dict:
    """
    The turn's status, warnings and downloadable tables, from its tool outputs.

    - ``status``: ``clarify`` when a query asked the user something, ``error``
      when queries ran and every one failed, else ``ok``. A refusal (personal
      data, not answerable) is ``ok``: the answer explains it.
    - ``warnings``: each distinct warning a query returned.
    - ``tables``: every result past :data:`CSV_MIN_ROWS` rows, whole.
    """
    queries = [out for name, out in results if name == "warehouse_query"]
    statuses = [q.get("status") for q in queries]
    if "needs_clarification" in statuses:
        status = "clarify"
    elif queries and all(s in FAILED_STATUSES for s in statuses):
        status = "error"
    else:
        status = "ok"

    warnings = []
    for q in queries:
        w = q.get("warning")
        if w and w not in warnings:
            warnings.append(w)

    tables = [
        {
            "columns": list(q.get("columns") or []),
            "rows": list(q.get("rows") or []),
            "row_count": int(q.get("row_count") or 0),
            "truncated": bool(q.get("truncated")),
            "sql": q.get("sql"),
        }
        for q in queries
        if q.get("status") == "ok" and int(q.get("row_count") or 0) > CSV_MIN_ROWS
    ]
    return {"status": status, "warnings": warnings, "tables": tables}
