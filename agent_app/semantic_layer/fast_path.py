# -*- coding: utf-8 -*-

"""
Answer a plain metric question from the semantic layer alone, with no SQL
writer call.

Two models run in series for every question: the agent, then the writer. For
"SLA breach rate by size tier over the last 12 months" the writer adds a
round trip to produce what the compiler already produces from the same
definitions. This module decides when that is so, conservatively: the
question must name exactly one metric, only dimensions and windows the layer
defines, and nothing else. Any word the layer does not account for -- a
tenant, a threshold, "only", a number -- sends the question to the writer,
which can read it. Wrong-and-fast is worse than slow, so the gate errs
toward the writer; the eval, with FAST_PATH on and off, is what says whether
the gate is right.
"""

import dataclasses
import re
import typing as T

from .compiler import CompileError, compile_metric
from .registry import Match, SemanticRegistry

#: Words that carry no condition. Anything left after these and the matched
#: phrases are removed is a condition the compiler cannot express.
FILLER = frozenset("""
a an and are as by each every for give how in is me of on over per please
show tell the this to us what whats what's which with across all overall
total platform wide platform-wide breakdown break down split grouped group
rate rates number count our current now trend
""".split())

#: A digit anywhere: a threshold, a top N, a date, a year. The writer's job.
_DIGIT = re.compile(r"\d")
_QUOTE = re.compile(r"[\"'`]")
_WORD = re.compile(r"[a-z][a-z\-']*")


@dataclasses.dataclass(frozen=True)
class FastPlan:
    metric: str
    dimensions: list[str]
    time_window: str | None
    #: PostgreSQL, like the writer's: the guard transpiles it.
    sql: str
    warnings: list[str]
    #: What the question said beyond the layer's vocabulary; empty when planned.
    residue: list[str] = dataclasses.field(default_factory=list)


def _phrases(name: str, display_name: str, synonyms: T.Iterable[str]) -> list[str]:
    phrases = [name.replace("_", " "), display_name, *synonyms]
    # Longest first, so "sla breach rate" goes before "breach".
    return sorted({p.lower() for p in phrases if p}, key=len, reverse=True)


def unmatched_text(intent: str, match: Match) -> str:
    """``intent`` with every matched phrase blanked out: what the layer did not account for."""
    text = intent.lower()
    phrases: list[str] = []
    for m in match.metrics:
        phrases += _phrases(m.name, m.display_name, m.synonyms)
    for d in match.dimensions:
        phrases += _phrases(d.name, d.display_name, d.synonyms)
    for w in match.time_windows:
        phrases += _phrases(w.name, w.display_name, w.synonyms)
    for g in match.glossary:
        phrases += _phrases(g.term, g.term, g.synonyms)
    for phrase in sorted(set(phrases), key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", " ", text)
    return text


def residue(intent: str, registry: SemanticRegistry, match: Match) -> list[str]:
    """The words of ``intent`` that neither the matched vocabulary nor filler accounts for."""
    return [word for word in _WORD.findall(unmatched_text(intent, match)) if word not in FILLER]


def plan_fast_path(registry: SemanticRegistry, intent: str, match: Match) -> FastPlan | None:
    """
    The compiled query for ``intent``, or None when the writer should see it.

    ``match`` is the resolved match (text plus what the agent named). Returns
    None, never raises: every reason not to is a reason to let the writer try.
    """
    if len(match.metrics) != 1 or len(match.time_windows) > 1:
        return None
    if any(g.read_from for g in match.glossary):
        # A term answered from its own table (a health snapshot): the metric
        # would recompute what that table holds.
        return None
    # Digits and quotes are judged on what is left once the matched phrases
    # are gone: the 12 in "last 12 months" names a window the layer defines,
    # the 2026 in "May 2026" or the 50 in "at least 50" does not. On the
    # first eval with the gate on, every question with a defined window was
    # turned away by its own digit, and the fast path never ran.
    left_text = unmatched_text(intent, match)
    if _DIGIT.search(left_text) or _QUOTE.search(left_text):
        return None
    if residue(intent, registry, match):
        return None
    metric = match.metrics[0]
    dimensions = [d.name for d in match.dimensions]
    window = match.time_windows[0].name if match.time_windows else None
    try:
        compiled = compile_metric(registry, metric.name, window, dimensions)
    except CompileError:
        return None
    return FastPlan(
        metric=metric.name,
        dimensions=dimensions,
        time_window=window,
        sql=compiled.sql,
        warnings=list(compiled.warnings),
    )
