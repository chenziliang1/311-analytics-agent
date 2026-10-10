# -*- coding: utf-8 -*-

"""
Query results the answer refers to by id, instead of retyping.

A result the warehouse tool returns is named (``r1``, ``r2``, ... within a
turn) by the turn observer, which keeps the whole of it. The model sees the
first :data:`PREVIEW_ROWS` rows and the id, and refers to the result where the
rows should appear::

    [RESULT:r1]

The browser puts the rows there, from the ``table`` data part that carries the
same id; a chart names the id and the columns to draw, and the observer fills
the chart's numbers in from the kept result (see ``turn_observer``). Neither
path has the model copy a number by hand, which is where a transposed digit
or an invented row used to come from.

Text-only readers of an answer (the eval's judge, a log) get the same table
written out by :func:`expand_result_refs`.
"""

import re
import typing as T

#: How many rows the model sees of a result. Enough to summarize and to pick
#: the top of a ranking; past this the rows are a table, not prose.
PREVIEW_ROWS = 50

#: A reference on a line of its own: ``[RESULT:r1]``.
RESULT_REF = re.compile(r"^[ \t]*\[RESULT:([A-Za-z0-9_-]+)\][ \t]*$", re.M)


def result_id(n: int) -> str:
    return f"r{n}"


def preview(output: dict) -> dict:
    """The model's copy of a result: the first rows, and a note that they are."""
    rows = list(output.get("rows") or [])
    if len(rows) <= PREVIEW_ROWS:
        return dict(output)
    return {**output, "rows": rows[:PREVIEW_ROWS], "preview": True}


def _cell(value: T.Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:.6g}"
        return text if "e" not in text else repr(value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_table(columns: T.Sequence[str], rows: T.Sequence[T.Sequence[T.Any]], row_count: int | None = None) -> str:
    """The result as a Markdown table, with a line for rows not shown."""
    lines = ["| " + " | ".join(_cell(c) for c in columns) + " |", "|" + " --- |" * len(columns)]
    for row in rows:
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    total = row_count if row_count is not None else len(rows)
    if total > len(rows):
        lines.append(f"\n({len(rows)} of {total} rows shown)")
    return "\n".join(lines)


def expand_result_refs(answer: str, tables: T.Iterable[dict], max_rows: int = 200) -> str:
    """
    ``[RESULT:id]`` lines replaced by the Markdown table of that result.

    An id with no table is left as written: a reader then sees the reference
    rather than nothing, which is also what the eval should notice.
    """
    by_id = {t.get("result_id"): t for t in tables if t.get("result_id")}

    def replace(match: re.Match) -> str:
        table = by_id.get(match.group(1))
        if table is None:
            return match.group(0)
        rows = list(table.get("rows") or [])
        return markdown_table(table.get("columns") or [], rows[:max_rows], table.get("row_count") or len(rows))

    return RESULT_REF.sub(replace, answer or "")


def referenced_ids(answer: str) -> list[str]:
    """The result ids an answer refers to, in order, once each."""
    return list(dict.fromkeys(RESULT_REF.findall(answer or "")))
