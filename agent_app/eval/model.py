# -*- coding: utf-8 -*-

"""
A golden case: a conversation, the query whose result is its ground truth, and
which parts of that result an answer must reproduce.

The ground truth is a query, not a stored table of numbers. It is run against
the database at eval time, so the expected values can never drift from the data
they describe, and a case reads as "this question means this SQL", which is the
thing a reviewer can actually check.
"""

import typing as T
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MatchSpec(_Strict):
    """
    Which golden rows the agent's result must contain, and how to recognise them.

    Matching ignores column names and order: a golden row is found when one
    result row holds every one of its ``labels`` as a cell and every one of its
    ``values`` as a number. That survives the writer naming a column
    differently, pivoting months into columns, or returning a percentage where
    the reference returned a rate.
    """

    #: Golden columns whose text identifies a row (``tenant_code``, ``month``).
    #: Leave out 0/1 flags: an answer may label them in words.
    labels: list[str] = Field(default_factory=list)
    #: Golden numeric columns every matched row must reproduce.
    values: list[str]
    #: Keep only golden rows equal to one of these column -> value maps.
    where: list[dict[str, T.Any]] = Field(default_factory=list)
    #: Keep only the first N golden rows (after ``where``).
    top: int | None = None
    #: Matched rows must appear in the golden order.
    ordered: bool = False


class GoldenCase(_Strict):
    id: str
    #: Where the ground truth comes from, e.g. ``Q8`` in the dataset's SQL doc.
    source: str
    #: Paraphrases of the final user turn. Each runs as its own conversation.
    questions: list[str]
    #: Earlier user turns, answered by the agent before the final one.
    history: list[str] = Field(default_factory=list)
    reference_sql: str
    match: MatchSpec
    #: Metrics the answer involves; such an answer should say how it was computed.
    metrics: list[str] = Field(default_factory=list)


def load_golden_cases(path: Path) -> list[GoldenCase]:
    """Raises on the first invalid case, naming it."""
    cases = []
    seen = set()
    for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        case = GoldenCase.model_validate(raw)
        if case.id in seen:
            raise ValueError(f"duplicate golden case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases
