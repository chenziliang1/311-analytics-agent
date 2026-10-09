# -*- coding: utf-8 -*-

"""
Turn a metric, a time window and some dimensions into one SELECT.

This is the semantic layer's whole promise made executable: a metric means the
same thing in every query because every query is assembled from the same
fragments. Pure string assembly, no database access, so it can be tested
without a connection.
"""

import dataclasses
import datetime

from .model import Metric
from .registry import SemanticRegistry


class CompileError(ValueError):
    """A combination the layer cannot express, e.g. a dimension with no join path."""


@dataclasses.dataclass(frozen=True)
class CompiledQuery:
    sql: str
    #: Changes the compiler made to the request, to be shown to the user.
    warnings: list[str]


#: Bounds substituted into ``{window_start}`` / ``{window_end}`` when no window
#: is requested, so a join written for a window still compiles without one.
_OPEN_START = datetime.date(1900, 1, 1)
_OPEN_END = datetime.date(9999, 12, 31)


@dataclasses.dataclass(frozen=True)
class _Window:
    #: The WHERE fragment, or None when nothing is filtered.
    filter: str | None
    start: datetime.date
    end: datetime.date
    warnings: list[str]


def _resolve_window(
    metric: Metric, registry: SemanticRegistry, window_name: str | None
) -> _Window:
    if window_name is None:
        return _Window(None, _OPEN_START, _OPEN_END, [])
    window = registry.time_windows.get(window_name)
    if window is None:
        raise CompileError(f"unknown time window {window_name!r}")
    if metric.time_column is None:
        return _Window(
            None,
            _OPEN_START,
            _OPEN_END,
            [f"{metric.display_name} has no time axis; the time window was ignored."],
        )

    column = metric.time_column
    if window.anchor == "closed" and metric.closed_time_column:
        column = metric.closed_time_column

    warnings = []
    end = window.end
    if metric.window_end_cap and end > metric.window_end_cap:
        end = metric.window_end_cap
        warnings.append(
            f"The window was cut to end on {end.isoformat()} so every row has its "
            f"full follow-up period for {metric.display_name}."
        )
    start = window.start.isoformat()
    return _Window(
        f"{column} >= '{start}' AND {column} < '{end.isoformat()}'",
        window.start,
        end,
        warnings,
    )


def compile_metric(
    registry: SemanticRegistry,
    metric_name: str,
    time_window: str | None = None,
    dimensions: list[str] | tuple[str, ...] = (),
    min_rows: int | None = None,
    limit: int | None = None,
) -> CompiledQuery:
    """
    Assemble the SELECT for one metric, grouped by ``dimensions``.

    Each result row also carries ``base_rows``, the number of rows the metric
    was computed over, because a rate over a handful of rows reads exactly like
    a rate over thousands. ``min_rows`` drops groups below that size.
    """
    metric = registry.metrics.get(metric_name)
    if metric is None:
        raise CompileError(f"unknown metric {metric_name!r}")

    window = _resolve_window(metric, registry, time_window)
    # A window function in a derived table cannot see the outer WHERE, so a
    # metric whose "next row" must come from the same window says so with these
    # two placeholders.
    joins = [
        j.replace("{window_start}", window.start.isoformat()).replace(
            "{window_end}", window.end.isoformat()
        )
        for j in metric.joins
    ]
    present = list(metric.aliases)
    select, group_by = [], []
    for dim_name in dimensions:
        dim = registry.dimensions.get(dim_name)
        if dim is None:
            raise CompileError(f"unknown dimension {dim_name!r}")
        if dim.sensitive:
            raise CompileError(
                f"{dim.display_name} is personal data and cannot be selected."
            )
        if dim.alias and dim.alias not in present:
            clause = next(
                (c for via, c in dim.joins.items() if via in present), None
            )
            if clause is None:
                raise CompileError(
                    f"{dim.display_name} cannot be combined with {metric.display_name}."
                )
            joins.append(clause)
            present.append(dim.alias)
        if "{time_column}" in dim.expression:
            if metric.time_column is None:
                raise CompileError(
                    f"{dim.display_name} needs a time axis and "
                    f"{metric.display_name} has none."
                )
            expression = dim.expression.replace("{time_column}", metric.time_column)
        else:
            expression = dim.expression
        select.append(f"{expression} AS {dim.name}")
        group_by.append(expression)

    where = list(metric.filters) + ([window.filter] if window.filter else [])

    lines = ["SELECT " + ",\n       ".join(
        select + ["COUNT(*) AS base_rows", f"{metric.formula.strip()} AS {metric.name}"]
    )]
    lines.append(f"FROM {metric.base_table}")
    lines.extend(joins)
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))
    if min_rows:
        lines.append(f"HAVING COUNT(*) >= {int(min_rows)}")
    if group_by:
        lines.append(f"ORDER BY {metric.name} DESC")
    if limit:
        lines.append(f"LIMIT {int(limit)}")
    return CompiledQuery(sql="\n".join(lines) + ";", warnings=window.warnings)
