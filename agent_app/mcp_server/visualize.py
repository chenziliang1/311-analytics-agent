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

from .models import ChartSeries, VisualizeOutput

SUPPORTED_TYPES = ("bar", "line")
MAX_SERIES = 4
MAX_CATEGORIES = 24


def build_chart(
    chart_type: str,
    title: str,
    categories: list[str],
    series: list[ChartSeries],
    y_label: str = "",
) -> VisualizeOutput:
    if chart_type not in SUPPORTED_TYPES:
        return VisualizeOutput(
            status="unsupported",
            chart_type=chart_type,
            error=f"only {' and '.join(SUPPORTED_TYPES)} charts render; use a Markdown table instead",
        )

    def fail(error: str) -> VisualizeOutput:
        return VisualizeOutput(status="render_error", chart_type=chart_type, error=error)

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
    return VisualizeOutput(status="ok", chart_type=chart_type, block=block)
