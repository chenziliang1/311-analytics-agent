# -*- coding: utf-8 -*-

"""
A hard cap on tool calls per question.

The system prompt asks for at most five, and a prompt is a request. A model
that keeps re-querying -- rephrasing the same intent after each result it does
not like -- costs a SQL-writer call each time and can loop until the request
times out. This hook makes the cap a fact: past it, a tool call is cancelled
with a message telling the model to answer from what it already has.
"""

from strands.hooks import BeforeInvocationEvent, BeforeToolCallEvent, HookProvider, HookRegistry

#: Knowledge, query, chart is three; five leaves room
#: for one clarification round.
DEFAULT_MAX_TOOL_CALLS = 5

BUDGET_MESSAGE = (
    "Tool budget for this question is used up ({limit} calls). Do not call any "
    "more tools: answer now from the results you already have, and say what "
    "you could not check."
)


class ToolCallBudget(HookProvider):
    def __init__(self, limit: int = DEFAULT_MAX_TOOL_CALLS):
        self.limit = limit
        self.calls = 0

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeToolCallEvent, self._count)

    def _reset(self, event: BeforeInvocationEvent) -> None:
        self.calls = 0

    def _count(self, event: BeforeToolCallEvent) -> None:
        self.calls += 1
        if self.calls > self.limit:
            event.cancel_tool = BUDGET_MESSAGE.format(limit=self.limit)
