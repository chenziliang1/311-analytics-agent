# -*- coding: utf-8 -*-

"""
Turning what the agent said into what the UI shows.

On the critical path of every reply, which is why it is not in
``agent_debugger.py`` -- that one only prints.

Strands does not hand back a clean answer. It hands back the message list it
built, thinking still inline as ``<thinking>`` tags, split across however many
messages the tool calls took. :func:`parse_agent_response` is the whole pipeline.
"""

import re

#: Fenced-block languages the chat UI renders as something other than code.
#: Each is also the tag name a model reaches for when it drifts from the fence.
#: One of the three halves of the rendering contract -- the others are the
#: lookup table in ``components/chat/blocks/index.ts`` and the instructions in
#: ``data/system-prompt.md``.
RENDERABLE_BLOCKS = ("chart", "mermaid")

_THINKING = r"<thinking>(.*?)</thinking>"


def extract_text_from_messages(messages: list, start_index: int = 0) -> str:
    """
    Collect thinking from every assistant message, plus the last real answer.

    The answer comes from the last assistant message that still has text once
    the thinking is stripped, **which is not always the last message**. An agent
    that answers and then calls ``write_debug_report`` often signs off with
    nothing but ``<thinking>``; reading the final message unconditionally yields
    an empty answer, and the UI shows a reasoning block with no reply under it,
    which reads to the user as a hang.
    """
    all_thinking = []
    last_answer_text = None

    for msg in messages[start_index:]:
        if msg.get("role") != "assistant":
            continue
        for item in msg.get("content", []):
            if not (isinstance(item, dict) and "text" in item):
                continue
            text = item["text"]
            all_thinking.extend(
                m.strip() for m in re.findall(_THINKING, text, re.DOTALL) if m.strip()
            )
            stripped = re.sub(_THINKING, "", text, flags=re.DOTALL).strip()
            if stripped:
                last_answer_text = stripped

    texts = []
    if all_thinking:
        texts.append("<thinking>" + "\n\n".join(all_thinking) + "</thinking>")
    if last_answer_text:
        texts.append(last_answer_text)
    return "\n".join(texts)


def parse_response_text(full_text: str) -> tuple[str, str]:
    """
    Split text into (thinking, answer) by pulling out the ``<thinking>`` blocks.
    """
    thinking = "\n\n".join(
        m.strip() for m in re.findall(_THINKING, full_text, re.DOTALL)
    )

    answer = re.sub(_THINKING, "", full_text, flags=re.DOTALL)
    # A heading with nothing under it, left behind when the model streams a
    # section header and then puts the body inside a thinking block.
    answer = re.sub(r"^#{1,6}\s*$", "", answer, flags=re.MULTILINE)
    answer = re.sub(r"\n{3,}", "\n\n", answer)

    return thinking, answer.strip()


def normalize_render_blocks(text: str) -> str:
    """
    Rewrite ``<chart>``/``<mermaid>`` tag pairs into the fences the UI renders.

    The prompt asks for fences, but smaller models already emit ``<thinking>``
    tags and generalize the habit, wrapping a perfectly good chart payload in
    ``<chart>``. Markdown treats that as an unknown HTML tag and drops it, so the
    reply says "here is the chart" above nothing at all.

    Prompt wording alone does not survive a model that small and the mapping is
    unambiguous, so it is done mechanically. Content already inside a fence is
    left alone: the pattern requires the tag to sit on its own line.
    """
    for name in RENDERABLE_BLOCKS:
        pattern = rf"^[ \t]*<{name}>[ \t]*\n(.*?)\n[ \t]*</{name}>[ \t]*$"
        text = re.sub(
            pattern,
            lambda m, n=name: f"```{n}\n{m.group(1).strip()}\n```",
            text,
            flags=re.DOTALL | re.MULTILINE,
        )
    return text


def parse_agent_response(
    messages: list,
    start_index: int = 0,
) -> tuple[str, str]:
    """
    An agent's messages -> the (thinking, answer) pair the UI streams.

    The three steps are one function so a caller cannot run two of them and
    forget the third. Skipping the normalize step in particular fails silently:
    the answer still arrives, with the chart missing from it.
    """
    thinking, answer = parse_response_text(
        extract_text_from_messages(messages, start_index)
    )
    return thinking, normalize_render_blocks(answer)
