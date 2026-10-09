# -*- coding: utf-8 -*-

"""
Prove every metric still reproduces its reference value on the real database.

A formula edit that changes a number is a definition change, and a definition
change has to be deliberate. Replaying each metric against the value it was
signed off with is what turns "someone tidied the SQL" into a failing check
instead of a quietly different answer.

Needs a database, so it is a command (``agent-app replay-semantic-layer``), not
a pytest test: no test in this repo opens a network connection.
"""

import dataclasses
import time
import typing as T

import sqlalchemy as sa

from .compiler import compile_metric
from .registry import SemanticRegistry


@dataclasses.dataclass(frozen=True)
class ReplayResult:
    metric: str
    expected: float
    #: None when the query failed; ``error`` then says why.
    actual: float | None
    tolerance: float
    seconds: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.actual is not None
            and abs(self.actual - self.expected) <= self.tolerance
        )


def replay(
    engine: sa.Engine, registry: SemanticRegistry, dialect: str = "postgres"
) -> list[ReplayResult]:
    """
    Run every metric that declares a ``replay`` block. Never raises.

    :param dialect: The warehouse's SQL dialect. Metrics are written in
        PostgreSQL and transpiled exactly as the agent's queries are.
    """
    from ..sql_guard import transpile_sql

    results = []
    for metric in registry.metrics.values():
        if metric.replay is None:
            continue
        query = compile_metric(registry, metric.name, metric.replay.time_window)
        started = time.monotonic()
        actual: T.Optional[float] = None
        error = None
        try:
            with engine.connect() as conn:
                row = conn.execute(sa.text(transpile_sql(query.sql, dialect))).mappings().one()
            actual = float(row[metric.name])
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        results.append(
            ReplayResult(
                metric=metric.name,
                expected=metric.replay.expected,
                actual=actual,
                tolerance=metric.replay.tolerance,
                seconds=time.monotonic() - started,
                error=error,
            )
        )
    return results
