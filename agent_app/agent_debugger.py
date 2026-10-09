# -*- coding: utf-8 -*-

"""
Driving the agent from a script or a REPL, with readable output.

Development only -- nothing here runs in the API. The parsing it prints is in
``agent_response.py``, which is on the critical path; this module is the part
that prints.

    from agent_app.api import one
    from agent_app.agent_debugger import chat

    agent = one.agent
    agent.messages.clear()
    thinking, answer = chat(agent, "Your question here", turn_number=1)
"""

import io
import sys

from strands import Agent

from .agent_response import parse_agent_response

RULE = "=" * 70
THIN_RULE = "-" * 70


def chat(  # pragma: no cover - drives a live agent
    agent: Agent,
    message: str,
    turn_number: int = 1,
    verbose: bool = False,
) -> tuple[str, str]:
    """
    Send one message and print the turn: request, thinking, answer.

    Goes through the same :func:`~agent_app.agent_response.parse_agent_response`
    the API uses, so what prints here is what a browser would have rendered.

    ``verbose`` leaves the agent's own streaming output on stdout; by default it
    is swallowed, since it interleaves with the formatting below and makes the
    transcript unreadable.
    """
    print(RULE)
    print(f"  TURN {turn_number}")
    print(RULE)
    print("\n[REQUEST]")
    print(THIN_RULE)
    print(message)

    msg_count_before = len(agent.messages)

    if verbose:
        agent(message)
    else:
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            agent(message)
        finally:
            sys.stdout = old_stdout

    thinking, answer = parse_agent_response(agent.messages, msg_count_before)

    if thinking:
        print("\n[THINKING]")
        print(THIN_RULE)
        print(thinking)

    print("\n[RESPONSE]")
    print(THIN_RULE)
    print(answer)
    print("\n")

    return thinking, answer


def print_summary(results: list[tuple[str, str, str]]) -> None:
    """Print one line per turn from a list of ``(name, thinking, answer)``."""
    print(RULE)
    print("  SUMMARY")
    print(RULE)
    for turn_name, thinking, answer in results:
        print(f"\n[{turn_name}]")
        print(f"  Thinking: {len(thinking)} chars")
        print(f"  Answer: {len(answer)} chars")
