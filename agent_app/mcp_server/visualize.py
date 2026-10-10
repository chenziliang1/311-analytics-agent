# -*- coding: utf-8 -*-

"""
Validate chart data and hand back the block the browser renders.

The chart is drawn in the browser, from a ```chart fenced block in the
answer, so it stays interactive and follows the site's styling. This tool's
job is to make sure the block is one the renderer will accept: a malformed
block renders as raw JSON text, with nothing telling the reader a chart was
meant to be there. The rules below mirror the renderer's.
"""

import json
import math
import typing as T

from .models import ChartSeries, VisualizeOutput

SUPPORTED_TYPES = ("bar", "line")
MAX_SERIES = 4
MAX_CATEGORIES = 24


def chart_data(
    columns: T.Sequence[str],
    rows: T.Sequence[T.Sequence[T.Any]],
    x: str,
    y: T.Sequence[str],
) -> tuple[list[str], list[ChartSeries]]:
    """
    A result's columns as a chart's categories and series: ``x`` names the
    column whose values label the axis, ``y`` the numeric columns to draw,
    one series each. The numbers come from the rows, never from the model.

    Raises ValueError with what to fix: a column that is not in the result,
    or a ``y`` column with a non-numeric or empty value.
    """
    names = [str(c) for c in columns]

    def index(column: str) -> int:
        matches = [i for i, c in enumerate(names) if c.lower() == str(column).lower()]
        if not matches:
            raise ValueError(f"column {column!r} is not in the result; its columns are {', '.join(names)}")
        return matches[0]

    xi = index(x)
    if not y:
        raise ValueError("y names no column")
    categories = [str(row[xi]) if row[xi] is not None else "" for row in rows]
    series = []
    for column in y:
        yi = index(column)
        values = []
        for row in rows:
            v = row[yi]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise ValueError(f"column {names[yi]!r} has a value that is not a number ({v!r}); pick a numeric column")
            values.append(float(v))
        series.append(ChartSeries(name=names[yi], values=values))
    return categories, series


def build_chart(
    chart_type: str,
    title: str,
    categories: list[str],
    series: list[ChartSeries],
    y_label: str = "",
    result_id: str | None = None,
) -> VisualizeOutput:
    if chart_type not in SUPPORTED_TYPES:
        return VisualizeOutput(
            status="unsupported",
            chart_type=chart_type,
            result_id=result_id,
            error=f"only {' and '.join(SUPPORTED_TYPES)} charts render; use a Markdown table instead",
        )

    def fail(error: str) -> VisualizeOutput:
        return VisualizeOutput(status="render_error", chart_type=chart_type, result_id=result_id, error=error)

    if not categories:
        return fail("no categories")
    if len(categories) > MAX_CATEGORIES:
        return fail(f"{len(categories)} categories; at most {MAX_CATEGORIES} render")
    if not series:
        return fail("no series")
    if len(series) > MAX_SERIES:
        return fail(f"{len(series)} series; at most {MAX_SERIES} render")
    for s in series:
        if len(s.values) != len(categories):
            return fail(
                f"series {s.name!r} has {len(s.values)} values for {len(categories)} categories"
            )
        if not all(math.isfinite(v) for v in s.values):
            return fail(f"series {s.name!r} has a value that is not a finite number")

    payload = {
        "type": chart_type,
        "title": title,
        "yLabel": y_label,
        "categories": [str(c) for c in categories],
        "series": [{"name": s.name, "values": s.values} for s in series],
    }
    block = "```chart\n" + json.dumps(payload, indent=2) + "\n```"
    return VisualizeOutput(status="ok", chart_type=chart_type, result_id=result_id, block=block)
