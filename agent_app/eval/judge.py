# -*- coding: utf-8 -*-

"""
The LLM-as-judge: is the answer right, and did it invent anything?

The judge sees what a careful human reviewer would: the question, the
reference result, every row the agent's tools returned, and the answer. It
does not see the agent's reasoning, so it grades what the user would read.
A fixed rubric and a JSON verdict keep its grading comparable across runs.
"""

import dataclasses
import json
import re
import typing as T

JUDGE_SYSTEM_PROMPT = """\
You grade answers from a business-intelligence agent over a 311 service request
warehouse. Be strict about numbers and lenient about wording.

You are given the conversation, the REQUIRED FACTS (rows and columns taken from
a reference query known to be correct), the TOOL RESULTS the agent actually
received, including any warning the tool attached and the DEFINITIONS it was
handed with them (what a metric or a term means, which the agent is told to
restate and cite), and the agent's ANSWER.

Decide two things:

1. correct: does the answer give the user every REQUIRED FACT?
   - Only the required facts decide correctness. Other numbers in the answer
     are judged under "hallucinated" alone.
   - Numbers may be rounded or shown as percentages (0.346 = 34.6%).
   - Identifiers may be prettified (santa_clara_ca = Santa Clara, CA).
   - Extra facts are fine if they are true.
   - A required fact that is missing, wrong, or attributed to the wrong row
     makes it incorrect. Order matters only when the facts are marked ordered,
     and rows with equal values may come in either order.
   - If the reference result is empty or the question cannot be answered, a
     clear statement of that is correct.

2. hallucinated: does the answer state any number or factual claim about the
   data that is NOT supported by the TOOL RESULTS (rows, warnings, or the query
   that ran, from this turn or an earlier one)? Arithmetic on tool values (a
   difference, a ratio, a share) counts as supported when the arithmetic is
   right, and so does repeating a tool warning. A number copied from nowhere, or
   a conclusion the tool results contradict, is a hallucination, even when it
   happens to be true. Describing in general words how a metric is defined, or
   how the platform works (what the Other bucket is, what a category means), is
   background, not a claim about the data; so is restating a DEFINITION the
   agent was handed, even where the query checks it differently. A number or a
   quantity about the data that comes from background knowledge rather than a
   tool result ("a million requests share 150,000 addresses") is a
   hallucination: the agent must not state data it did not query.

Reply with one JSON object and nothing else:
{"correct": true|false, "hallucinated": true|false, "reason": "<one sentence>"}
"""

#: Takes the full request text, returns the judge model's raw reply.
Judge = T.Callable[[str], str]


@dataclasses.dataclass(frozen=True)
class Verdict:
    #: None when the judge's reply could not be read.
    correct: bool | None
    hallucinated: bool | None
    reason: str


def _table(columns: list[str], rows: list[list], limit: int = 25) -> str:
    if not columns:
        return "(no rows)"
    lines = [" | ".join(columns)]
    lines += [" | ".join(str(v) for v in row) for row in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows")
    return "\n".join(lines)


def build_judge_request(
    turns: list[str],
    required_columns: list[str],
    required_rows: list[list],
    tool_outputs: list[dict],
    answer: str,
    unsupported: list[str],
    ordered: bool = False,
    earlier_outputs: list[dict] | None = None,
) -> str:
    parts = ["## Conversation (user turns, last one is graded)"]
    parts += [f"{i + 1}. {t}" for i, t in enumerate(turns)]
    parts.append(
        f"\n## REQUIRED FACTS ({'ordered' if ordered else 'any order'})\n"
        + _table(required_columns, required_rows)
    )
    parts.append("\n## TOOL RESULTS")
    if not tool_outputs:
        parts.append("(the agent called no data tool in this turn)")
    labelled = [(o, "this turn") for o in tool_outputs] + [
        (o, "an earlier turn") for o in earlier_outputs or []
    ]
    for i, (out, when) in enumerate(labelled, 1):
        notes = "".join(
            f"\n{key}: {out[key]}" for key in ("warning", "clarification") if out.get(key)
        )
        # The query is evidence too: "duplicates were excluded" is checkable
        # against it, not against the rows.
        query = f"\nquery:\n{out['sql']}" if out.get("sql") else ""
        parts.append(
            f"### Result {i} ({when}): status {out.get('status')}{notes}{query}\n"
            + _table(out.get("columns") or [], out.get("rows") or [])
        )
        # The definitions the tool handed over with the result, which the
        # agent is told to restate in "How this was computed" and cite. Not
        # shown, the judge read a restated definition as an invented claim.
        for source in out.get("sources") or []:
            if isinstance(source, dict) and source.get("text"):
                parts.append(
                    f"#### DEFINITION given with Result {i}: {source.get('title') or source.get('id')}\n"
                    + str(source["text"]).strip()
                )
    parts.append("\n## ANSWER\n" + answer)
    if unsupported:
        parts.append(
            "\n## Numbers in the answer that match no tool value exactly "
            "(check whether they are correct arithmetic or invented)\n"
            + ", ".join(unsupported)
        )
    return "\n".join(parts)


def parse_verdict(reply: str) -> Verdict:
    found = re.search(r"\{.*\}", reply, re.S)
    if not found:
        return Verdict(None, None, f"unreadable judge reply: {reply[:200]}")
    try:
        data = json.loads(found.group(0))
    except json.JSONDecodeError:
        return Verdict(None, None, f"unreadable judge reply: {reply[:200]}")
    return Verdict(
        correct=data.get("correct") if isinstance(data.get("correct"), bool) else None,
        hallucinated=data.get("hallucinated") if isinstance(data.get("hallucinated"), bool) else None,
        reason=str(data.get("reason", "")),
    )
