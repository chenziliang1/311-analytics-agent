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
- **Results by id, numbers never retyped.** Every ok result is named here
  (``r1``, ``r2``, ...) and kept whole; the model gets the id and a preview
  of the rows (see ``results``). An answer puts ``[RESULT:r1]`` where the
  table belongs and the browser draws it from the kept rows, and a visualize
  call names the id and the columns, and its categories and series are
  filled in here from the kept rows before the call goes out.

Registered on the agent as a hook provider. One agent serves one turn at a
time (see ``chat._AGENT_LOCK``), so per-turn state on the observer is safe.
"""

import json
import typing as T
import weakref

from .mcp_server.visualize import chart_data
from .results import preview, result_id

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
        #: (tool name, parsed output) for every tool call this turn. A
        #: warehouse_query output is the whole result, with its ``result_id``.
        self.results: list[tuple[str, dict]] = []
        #: The ok results of this turn by id, whole.
        self.by_id: dict[str, dict] = {}
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
        self.by_id = {}
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
        if name == "visualize":
            event.tool_use["input"] = bind_chart(event.tool_use.get("input") or {}, self.by_id)
        tool_input = event.tool_use.get("input") or {}
        detail = tool_input.get("intent") if isinstance(tool_input, dict) else None
        self._emit(event.tool_use.get("name") or "tool", detail)

    def _tool_result(self, event: AfterToolCallEvent) -> None:
        output = tool_output(event.result)
        if output is None:
            return
        name = event.tool_use.get("name") or ""
        if name == "warehouse_query" and output.get("status") == "ok":
            output = {**output, "result_id": result_id(len(self.by_id) + 1)}
            self.by_id[output["result_id"]] = output
            # The model's copy: the id and the first rows. The whole result
            # stays here for the browser and the number check.
            event.result = with_output(event.result, preview(output))
        self.results.append((name, output))


def bind_chart(tool_input: dict, results: T.Mapping[str, dict]) -> dict:
    """
    A visualize call's categories and series, from the result it names.

    Whatever the model put in ``categories`` and ``series`` is replaced: the
    chart's numbers come from the kept rows or not at all. A result id the
    turn does not have, or a column the result lacks, becomes ``data_error``,
    which the tool reports back as a render_error with what to fix.
    """
    bound = {**tool_input, "categories": [], "series": [], "data_error": ""}
    rid = str(tool_input.get("result_id") or "")
    result = results.get(rid)
    if result is None:
        known = ", ".join(results) or "none this turn"
        bound["data_error"] = f"no result {rid!r} in this turn (results: {known}); pass the result_id warehouse_query returned"
        return bound
    y = tool_input.get("y")
    try:
        categories, series = chart_data(
            result.get("columns") or [], result.get("rows") or [], str(tool_input.get("x") or ""),
            [str(c) for c in (y if isinstance(y, list) else [y] if y else [])],
        )
    except ValueError as e:
        bound["data_error"] = str(e)
        return bound
    bound["categories"] = categories
    bound["series"] = [{"name": s.name, "values": s.values} for s in series]
    return bound


def with_output(result: T.Any, output: dict) -> T.Any:
    """
    ``result`` (a strands tool result) carrying ``output`` instead of its
    own, in both places strands keeps it: ``structuredContent`` and the JSON
    text block. Anything else in the result is kept.
    """
    if not isinstance(result, dict):
        return result
    replaced = dict(result)
    if isinstance(result.get("structuredContent"), dict):
        replaced["structuredContent"] = output
    content = []
    for block in result.get("content") or []:
        text = block.get("text") if isinstance(block, dict) else None
        if text:
            try:
                if isinstance(json.loads(text), dict):
                    content.append({**block, "text": json.dumps(output, default=str)})
                    continue
            except ValueError:
                pass
        content.append(block)
    replaced["content"] = content
    return replaced


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
    - ``tables``: every ok result with rows, whole, with its ``result_id``:
      the browser shows one where the answer writes ``[RESULT:id]``, and
      offers a download for one past :data:`CSV_MIN_ROWS` rows.
    - ``values``: the numbers the queries returned, once each, at most
      :data:`TURN_VALUES`. The browser sends them back with the history, so
      a later turn's numbers are checked against what the queries returned,
      not against what an earlier answer said.
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
            "result_id": q.get("result_id"),
            "columns": list(q.get("columns") or []),
            "rows": list(q.get("rows") or []),
            "row_count": int(q.get("row_count") or 0),
            "truncated": bool(q.get("truncated")),
            "download": int(q.get("row_count") or 0) > CSV_MIN_ROWS,
            "sql": q.get("sql"),
        }
        for q in queries
        if q.get("status") == "ok" and q.get("rows")
    ]
    values: dict[float, None] = {}
    for q in queries:
        for row in q.get("rows") or []:
            for cell in row:
                if isinstance(cell, (int, float)) and not isinstance(cell, bool) and len(values) < TURN_VALUES:
                    values.setdefault(float(cell), None)
    return {"status": status, "warnings": warnings, "tables": tables, "values": list(values)}


#: How many of a turn's returned numbers travel back with the history. Past
#: this a result is a table, and no later answer quotes its thousandth cell.
TURN_VALUES = 500
