# -*- coding: utf-8 -*-

"""
Result-set equivalence: does what the agent's query returned contain what the
reference query returned?

"Same SQL" is the wrong test -- two correct queries rarely look alike. "Same
result" is the right one, loosened only where a correct answer legitimately
differs: column names, column order, extra columns, rounding, and a rate shown
as a percentage.
"""

import dataclasses
import typing as T

from .model import MatchSpec


def as_number(value: T.Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def numbers_close(actual: float, expected: float) -> bool:
    """
    Equal up to display rounding, in either rate or percentage form.

    A count must match to the unit; a rate to about half a unit in its third
    decimal, which is what rounding to 3 places can cost.
    """
    if float(expected).is_integer() and abs(expected) >= 2:
        tolerance = max(0.5, abs(expected) * 0.001)
    else:
        tolerance = max(abs(expected) * 0.005, 0.0006)
    return any(
        abs(candidate - expected) <= tolerance
        for candidate in (actual, actual / 100.0, actual * 100.0)
    )


def _norm(value: T.Any) -> str:
    return str(value).strip().lower()


def _cell_equals(value: T.Any, wanted: T.Any) -> bool:
    a, b = as_number(value), as_number(wanted)
    if a is not None and b is not None:
        return a == b
    return _norm(value) == _norm(wanted)


def golden_rows(columns: list[str], rows: list[list], spec: MatchSpec) -> list[dict]:
    """The reference rows an answer must reproduce, as column -> value maps."""
    records = [dict(zip(columns, row)) for row in rows]
    if spec.where:
        records = [
            r for r in records
            if any(all(_cell_equals(r.get(k), v) for k, v in cond.items()) for cond in spec.where)
        ]
    if spec.top is not None:
        records = records[: spec.top]
    return records


def _row_matches(golden: dict, row: list, spec: MatchSpec, taken: set[int]) -> set[int] | None:
    """
    The cells of ``row`` that reproduce ``golden``'s values, or None.

    Cells, not rows, are what a golden row consumes: a pivoted result holds
    several golden rows in one row (one month, a column per channel), while two
    identical golden rows must still find two separate cells.
    """
    cells = [_norm(c) for c in row]
    if not all(_norm(golden[label]) in cells for label in spec.labels):
        return None
    used: set[int] = set()
    for column in spec.values:
        expected = as_number(golden[column])
        if expected is None:
            continue
        hit = next(
            (
                i for i, cell in enumerate(row)
                if i not in taken and i not in used
                and (n := as_number(cell)) is not None and numbers_close(n, expected)
            ),
            None,
        )
        if hit is None:
            return None
        used.add(hit)
    return used


@dataclasses.dataclass(frozen=True)
class MatchOutcome:
    ok: bool
    expected_rows: int
    matched_rows: int
    #: Golden rows that found no match, for the report.
    missing: list[dict]


def unpivoted(columns: list[str], rows: list[list]) -> list[list]:
    """
    Each cell as its own row: the original row, then that column's name and
    value.

    A result pivoted wide, one column per category ("council_letter",
    "news_media", ...), holds the same facts as a long reference with a
    category column, but its labels are column names, which matching never
    looks at. In this form each name sits in a cell next to its value.
    """
    return [[*row, name, row[i]] for row in rows for i, name in enumerate(columns)]


def match_result(
    golden_columns: list[str],
    golden_values: list[list],
    agent_rows: list[list],
    spec: MatchSpec,
) -> MatchOutcome:
    """
    Every selected golden row must match agent cells no other golden row has
    used; with ``ordered``, in the same row order. Greedy, which is enough: a
    row that carries a golden row's labels and all its values is that row.
    """
    expected = golden_rows(golden_columns, golden_values, spec)
    taken: dict[int, set[int]] = {}
    placed: list[tuple[tuple, int]] = []
    missing = []
    for golden in expected:
        found = None
        for i, row in enumerate(agent_rows):
            cells = _row_matches(golden, row, spec, taken.get(i, set()))
            if cells is not None:
                found = i
                taken.setdefault(i, set()).update(cells)
                break
        if found is None:
            missing.append(golden)
        else:
            key = tuple(round(as_number(golden[c]) or 0.0, 6) for c in spec.values)
            placed.append((key, found))
    # Rows tied on every value may come in either order: the reference's own
    # order between them is arbitrary too.
    in_order = not spec.ordered or all(
        pos_a <= pos_b
        for n, (key_a, pos_a) in enumerate(placed)
        for key_b, pos_b in placed[n + 1:]
        if key_a != key_b
    )
    return MatchOutcome(
        ok=bool(expected) and not missing and in_order,
        expected_rows=len(expected),
        matched_rows=len(placed),
        missing=missing,
    )
