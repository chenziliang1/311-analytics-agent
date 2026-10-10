# -*- coding: utf-8 -*-

"""
Run golden conversations through the agent and score each one.

The agent is reached through a ``Chat`` callable: the real one posts to the
same ``/api/chat`` endpoint the browser uses, so the eval measures the product
rather than a test double of it; tests pass a scripted one.
"""

import dataclasses
import typing as T

from ..knowledge import citations
from .grounding import unsupported_numbers
from .judge import Judge, Verdict, build_judge_request, parse_verdict
from .matching import golden_rows, match_result, unpivoted
from .model import GoldenCase


@dataclasses.dataclass
class Turn:
    answer: str
    seconds: float
    #: (tool name, input) for every tool call the agent made in this turn.
    tool_calls: list[tuple[str, dict]]
    #: Parsed warehouse_query outputs, in call order.
    query_outputs: list[dict]


#: Takes the conversation so far as (role, text) pairs ending with the user's
#: turn, runs one agent turn, returns what happened.
Chat = T.Callable[[list[tuple[str, str]]], Turn]

#: Takes a query, returns (columns, rows) from the database.
Reference = T.Callable[[str], tuple[list[str], list[list]]]

#: Takes a source id, says whether the knowledge base could hold it.
Knows = T.Callable[[str], bool]


def cited_correctly(answer: str, case_metrics: T.Sequence[str], knows: Knows | None) -> bool | None:
    """
    The answer cites the definition its number rests on.

    None when the case involves no metric. False when it cites nothing, cites
    a source the knowledge base does not have (an invented one), or cites
    only sources other than the case's metrics. Presence of a "how this was
    computed" sentence is not enough: a citation names one document, and a
    wrong one is checkable.
    """
    if not case_metrics:
        return None
    cited = citations(answer)
    if not cited:
        return False
    if knows is not None and any(not knows(c) for c in cited):
        return False
    return any(f"metric/{m}" in cited for m in case_metrics)


@dataclasses.dataclass
class CaseScore:
    case_id: str
    question: str
    #: Some warehouse_query result in the graded turn reproduces the reference.
    sql_ok: bool
    #: Judge verdicts; None when the judge was off or unreadable.
    answer_ok: bool | None
    hallucinated: bool | None
    #: The answer cites the metric it rests on (cited_correctly). None when no
    #: metric applies.
    cited: bool | None
    seconds: float
    statuses: list[str]
    unsupported: list[str]
    judge_reason: str = ""
    missing_rows: int = 0
    error: str | None = None


def run_case(
    case: GoldenCase,
    question: str,
    chat: Chat,
    reference: Reference,
    judge: Judge | None,
    knows: Knows | None = None,
) -> CaseScore:
    """One conversation: earlier turns first, then the graded one. Never raises."""
    try:
        conversation: list[tuple[str, str]] = []
        # Earlier turns' results are part of what the user was shown, so an
        # answer may legitimately repeat them.
        earlier_outputs: list[dict] = []
        for earlier in case.history:
            conversation.append(("user", earlier))
            earlier_turn = chat(conversation)
            earlier_outputs += earlier_turn.query_outputs
            conversation.append(("assistant", earlier_turn.answer))
        conversation.append(("user", question))
        turn = chat(conversation)

        ref_columns, ref_rows = reference(case.reference_sql)
        outcomes = []
        for out in turn.query_outputs:
            if out.get("status") != "ok":
                continue
            rows = out.get("rows") or []
            outcome = match_result(ref_columns, ref_rows, rows, case.match)
            if not outcome.ok:
                # The same facts pivoted wide: categories as column names.
                wide = match_result(
                    ref_columns, ref_rows, unpivoted(out.get("columns") or [], rows), case.match
                )
                outcome = wide if wide.ok else outcome
            outcomes.append(outcome)
        sql_ok = any(o.ok for o in outcomes)
        best_missing = min((len(o.missing) for o in outcomes), default=len(ref_rows))

        tool_values = [
            v for out in earlier_outputs + turn.query_outputs for row in out.get("rows") or [] for v in row
        ]
        unsupported = unsupported_numbers(turn.answer, tool_values, question)

        verdict = Verdict(None, None, "")
        if judge is not None:
            # The judge grades against exactly what the result check requires,
            # so the two scores disagree only when the answer misreports a
            # right result, never because they were shown different facts.
            required_columns = case.match.labels + case.match.values
            required_rows = [
                [row[c] for c in required_columns]
                for row in golden_rows(ref_columns, ref_rows, case.match)
            ]
            verdict = parse_verdict(
                judge(
                    build_judge_request(
                        list(case.history) + [question],
                        required_columns,
                        required_rows,
                        turn.query_outputs,
                        turn.answer,
                        unsupported,
                        ordered=case.match.ordered,
                        earlier_outputs=earlier_outputs,
                    )
                )
            )

        return CaseScore(
            case_id=case.id,
            question=question,
            sql_ok=sql_ok,
            answer_ok=verdict.correct,
            hallucinated=verdict.hallucinated,
            cited=cited_correctly(turn.answer, case.metrics, knows),
            seconds=turn.seconds,
            statuses=[str(o.get("status")) for o in turn.query_outputs],
            unsupported=unsupported,
            judge_reason=verdict.reason,
            missing_rows=best_missing,
        )
    except Exception as e:
        return CaseScore(
            case_id=case.id,
            question=question,
            sql_ok=False,
            answer_ok=None,
            hallucinated=None,
            cited=None,
            seconds=0.0,
            statuses=[],
            unsupported=[],
            error=f"{type(e).__name__}: {e}",
        )


def run_all(
    cases: list[GoldenCase],
    chat: Chat,
    reference: Reference,
    judge: Judge | None,
    on_score: T.Callable[[CaseScore], None] = lambda s: None,
    knows: Knows | None = None,
) -> list[CaseScore]:
    scores = []
    for case in cases:
        for question in case.questions:
            score = run_case(case, question, chat, reference, judge, knows)
            on_score(score)
            scores.append(score)
    return scores
