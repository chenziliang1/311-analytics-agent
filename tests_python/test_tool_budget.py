# -*- coding: utf-8 -*-

from types import SimpleNamespace

from agent_app.tool_budget import ToolCallBudget


def test_cancels_past_the_limit_and_resets_per_question():
    budget = ToolCallBudget(limit=2)
    events = [SimpleNamespace(cancel_tool=False) for _ in range(3)]
    for e in events:
        budget._count(e)
    assert [e.cancel_tool for e in events][:2] == [False, False]
    assert "used up (2 calls)" in events[2].cancel_tool

    budget._reset(SimpleNamespace())
    fresh = SimpleNamespace(cancel_tool=False)
    budget._count(fresh)
    assert fresh.cancel_tool is False


def test_registers_both_callbacks():
    from strands.hooks import BeforeInvocationEvent, BeforeToolCallEvent

    registered = []
    registry = SimpleNamespace(add_callback=lambda event, cb: registered.append(event))
    ToolCallBudget().register_hooks(registry)
    assert registered == [BeforeInvocationEvent, BeforeToolCallEvent]
