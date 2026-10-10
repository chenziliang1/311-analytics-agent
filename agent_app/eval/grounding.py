# -*- coding: utf-8 -*-

"""
Which numbers in an answer did no tool ever return?

The cheap half of the hallucination check. It over-reports on purpose -- a
ratio the agent derived ("25 times more likely") is not in any result and is
not a hallucination -- so its output is evidence handed to the judge, not a
verdict.
"""

import re
import typing as T

from .matching import as_number

# Thousands separators inside a number, never a comma after it: "987, then"
# is 987, which a question or an earlier answer may contain as "987".
_NUMBER = re.compile(r"(?<![\w.])-?\d(?:[\d,]*\d)?(?:\.\d+)?%?")
_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
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


def unsupported_numbers(answer: str, tool_values: T.Iterable[T.Any], question: str) -> list[str]:
    """
    Numbers shown in ``answer`` that match no tool value up to the rounding
    they are shown with.

    Skipped as never data: anything inside a ```sql block, dates, small whole
    numbers (list sizes, day counts, ranks), and numbers the question itself
    contains.
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
        if not _supported(value, decimals, is_percent, known):
            out.append(token)
    return out
