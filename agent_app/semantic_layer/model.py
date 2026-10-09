# -*- coding: utf-8 -*-

"""
The semantic layer's vocabulary: metrics, dimensions, time windows, glossary.

Each YAML file under ``data/semantic_layer/`` parses into exactly one of these,
so a typo in a field name fails at load time instead of producing a metric that
silently compiles to the wrong SQL.

SQL fragments are written against the table aliases a metric declares in
``base_table`` and ``joins``. The compiler never rewrites them; it only decides
which fragments go together.
"""

import datetime
import re
import typing as T

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The alias is the word right before ``ON`` in a join clause, or the last word
#: of a ``FROM`` item. Derived tables (``(SELECT ...) re ON ...``) follow the
#: same rule, which is why this is not a full SQL parse.
_JOIN_ALIAS = re.compile(r"(\w+)\s+ON\b", re.IGNORECASE)


def _join_alias(clause: str) -> str:
    found = _JOIN_ALIAS.findall(clause)
    if not found:
        raise ValueError(f"cannot find the table alias in join clause: {clause!r}")
    # The last match, so a derived table's own inner joins do not win.
    return found[-1]


class _Strict(BaseModel):
    # An unknown key is a typo, not an extension point.
    model_config = ConfigDict(extra="forbid")


class Replay(_Strict):
    """
    The value a metric must reproduce on the real database.

    ``expected`` comes from the hand-verified query in ``data/system-prompt.md``
    for the same metric and window, not from running this metric's own SQL, so
    a formula edit that changes the number fails the replay.
    """

    time_window: str
    expected: float
    tolerance: float = Field(gt=0)


class Metric(_Strict):
    name: str
    display_name: str
    description: str
    #: ``table alias``, e.g. ``service_requests sr``.
    base_table: str
    #: Full join clauses the formula depends on, in order.
    joins: list[str] = Field(default_factory=list)
    #: An aggregate SQL expression, one value per group.
    formula: str
    #: WHERE fragments that are part of the definition, ANDed together.
    filters: list[str] = Field(default_factory=list)
    #: The column a time window filters on. None for a metric with no time axis.
    time_column: str | None = None
    #: Used instead of ``time_column`` by windows anchored on closure.
    closed_time_column: str | None = None
    #: Latest window end that still gives every row its full follow-up period,
    #: e.g. 60 days for a council complaint to arrive.
    window_end_cap: datetime.date | None = None
    synonyms: list[str] = Field(default_factory=list)
    #: Definition traps, shown to whoever writes SQL from this metric.
    notes: str = ""
    replay: Replay | None = None

    @property
    def aliases(self) -> list[str]:
        return [self.base_table.split()[-1]] + [_join_alias(j) for j in self.joins]


class Dimension(_Strict):
    name: str
    display_name: str
    #: SQL expression to group by. ``{time_column}`` is replaced with the
    #: metric's time column, which is how ``month`` follows whatever the metric
    #: counts by.
    expression: str
    #: The alias ``expression`` reads from. None when it reads only the metric's
    #: own columns.
    alias: str | None = None
    #: Alias already present in the metric -> the join clause that brings
    #: ``alias`` in from it. Tried in order.
    joins: dict[str, str] = Field(default_factory=dict)
    #: ``table.column`` this dimension exposes. Required when ``sensitive``.
    source_column: str | None = None
    allowed_values: list[str] = Field(default_factory=list)
    #: Personal data. Never selected, never shown to a model.
    sensitive: bool = False
    synonyms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sensitive_names_its_column(self) -> "Dimension":
        if self.sensitive and not self.source_column:
            raise ValueError(f"sensitive dimension {self.name!r} needs source_column")
        return self


class TimeWindow(_Strict):
    name: str
    display_name: str
    #: Inclusive.
    start: datetime.date
    #: Exclusive. Literal dates, never CURRENT_DATE: answers must reproduce.
    end: datetime.date
    #: ``closed`` filters on a metric's ``closed_time_column`` when it has one.
    anchor: T.Literal["event", "closed"] = "event"
    synonyms: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _start_before_end(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError(f"time window {self.name!r} starts on or after it ends")
        return self


class GlossaryTerm(_Strict):
    term: str
    definition: str
    synonyms: list[str] = Field(default_factory=list)
    #: The table a question about this term is answered from. The warehouse
    #: tool sends a query that does not read it back to the writer once.
    read_from: str | None = None
