# -*- coding: utf-8 -*-

"""
Which numbers in an answer did no tool ever return?

The cheap half of the hallucination check. A number that no result holds and
that is not one step of arithmetic on two results is reported; what it cannot
tell is whether the arithmetic was the right arithmetic, or whether a
sentence miscounts the rows ("three of them are in Quebec"). So its output is
evidence handed to the judge, not a verdict.
"""

import re
import typing as T

from .matching import as_number

# Thousands separators inside a number, never a comma after it: "987, then"
# is 987, which a question or an earlier answer may contain as "987".
_NUMBER = re.compile(r"(?<![\w.])-?\d(?:[\d,]*\d)?(?:\.\d+)?%?")
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
#: A year on its own: the data's own years, never a result.
_YEAR = re.compile(r"(?:19|20)\d{2}")
_FENCED_SQL = re.compile(r"```sql.*?```", re.S | re.I)


def _decimals(token: str) -> int:
    digits = token.rstrip("%")
    return len(digits.split(".")[1]) if "." in digits else 0


def _supported(value: float, decimals: int, is_percent: bool, known: list[float]) -> bool:
    # Half a unit in the last shown place, plus float slack.
    half = 0.5 * 10 ** (-decimals) + 1e-9
    for k in known:
        for candidate in (k, k * 100.0) if is_percent or decimals else (k,):
            if abs(candidate - value) <= half:
                return True
    return False


#: How many returned values a derived number is looked for among. Pairs are
#: quadratic; past this, a result is a table nobody does arithmetic on by hand.
_DERIVATION_VALUES = 200


def _derived(value: float, decimals: int, is_percent: bool, known: list[float]) -> bool:
    """
    ``value`` is one step of arithmetic on two returned values: a difference,
    a sum, a ratio, or a share, in units or as a percentage. "3.2 times as
    many", "20.4 points lower" and "64% of them" are the agent doing its job,
    not inventing; what is left after this is a number from nowhere.
    """
    half = 0.5 * 10 ** (-decimals) + 1e-9
    values = list(dict.fromkeys(known))[:_DERIVATION_VALUES]
    scales = (1.0, 100.0) if is_percent or decimals else (1.0,)
    for i, a in enumerate(values):
        for b in values[i + 1:]:
            candidates = [a - b, b - a, a + b]
            if b:
                candidates.append(a / b)
            if a:
                candidates.append(b / a)
            for c in candidates:
                for scale in scales:
                    if abs(c * scale - value) <= half:
                        return True
    return False


def unsupported_numbers(answer: str, tool_values: T.Iterable[T.Any], question: str) -> list[str]:
    """
    Numbers shown in ``answer`` that match no tool value up to the rounding
    they are shown with.

    Skipped as never data: anything inside a ```sql block, dates and bare
    years ("May 2026"), small whole numbers (list sizes, day counts, ranks),
    and numbers the question itself contains.
    """
    known = [n for n in (as_number(v) for v in tool_values) if n is not None]
    text = _DATE.sub(" ", _FENCED_SQL.sub(" ", answer))
    asked = set(_NUMBER.findall(question))
    out = []
    for token in _NUMBER.findall(text):
        if token in asked:
            continue
        is_percent = token.endswith("%")
        value = as_number(token.rstrip("%"))
        if value is None:
            continue
        decimals = _decimals(token)
        if decimals == 0 and not is_percent and abs(value) <= 31:
            continue
        if _YEAR.fullmatch(token):
            continue
        if _supported(value, decimals, is_percent, known):
            continue
        if _derived(value, decimals, is_percent, known):
            continue
        out.append(token)
    return out
