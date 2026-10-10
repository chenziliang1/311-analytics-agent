# -*- coding: utf-8 -*-

"""
Two switches an operator can flip in seconds, without a deployment.

- ``kill``: the warehouse tool answers "temporarily unavailable" to every
  question. For the day a load went wrong, a model regression is suspected,
  or a cost runs away.
- ``suspend:<metric>``: questions that name one metric are refused, with the
  reason, while everything else keeps working. For the day one definition is
  found wrong and the rest are not.

They live in a warehouse table (``CITYPULSE_AUDIT.SWITCHES``: name, value,
reason, changed_at, changed_by), read by the tool every SWITCH_TTL seconds
through the service connection. Flipping one is one row written by an
operator (``agent-app switch``); the next question sees it. When the table
cannot be read the last reading stays in force: an unreadable warehouse
stops queries by itself, and must not also turn a suspension off.
"""

import dataclasses
import time
import typing as T

#: The switch that stops every query.
KILL = "kill"
#: Prefix of the switches that stop one metric: ``suspend:sla_breach_rate``.
SUSPEND = "suspend:"
#: Values that mean a switch is thrown.
ON = frozenset({"on", "true", "1", "yes"})


@dataclasses.dataclass(frozen=True)
class Switches:
    #: Why the tool is unavailable, or None when it is not.
    killed: str | None = None
    #: Metric name -> why questions about it are refused.
    suspended: T.Mapping[str, str] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: T.Iterable[tuple]) -> "Switches":
        """Rows of (name, value, reason); unknown names are ignored."""
        killed = None
        suspended: dict[str, str] = {}
        for row in rows:
            name, value, reason = (str(row[0]).strip().lower(), str(row[1] or "").strip().lower(),
                                   str(row[2] or "").strip() if len(row) > 2 else "")
            if value not in ON:
                continue
            if name == KILL:
                killed = reason or "no reason given"
            elif name.startswith(SUSPEND) and name[len(SUSPEND):]:
                suspended[name[len(SUSPEND):]] = reason or "no reason given"
        return cls(killed=killed, suspended=suspended)


NO_SWITCHES = Switches()


class SwitchReader:
    """
    The switches as of the last read, re-read every ``ttl_seconds``.

    ``source`` returns the table's rows; a failure keeps the last good value
    and reports to ``on_error``. Before any good read, nothing is thrown.
    """

    def __init__(
        self,
        source: T.Callable[[], T.Iterable[tuple]],
        ttl_seconds: float = 30.0,
        clock: T.Callable[[], float] = time.monotonic,
        on_error: T.Callable[[Exception], None] | None = None,
    ):
        self._source = source
        self._ttl = ttl_seconds
        self._clock = clock
        self._on_error = on_error
        self._current = NO_SWITCHES
        self._read_at: float | None = None

    def __call__(self) -> Switches:
        now = self._clock()
        if self._read_at is None or now - self._read_at >= self._ttl:
            try:
                self._current = Switches.from_rows(self._source())
            except Exception as e:
                if self._on_error is not None:
                    self._on_error(e)
            self._read_at = now
        return self._current

    def refresh(self) -> None:
        self._read_at = None


#: The table's columns, in the order the loader creates them.
COLUMNS = ("name", "value", "reason", "changed_at", "changed_by")


def upsert_sql(table: str) -> str:
    """The MERGE that sets one switch; parameters name, value, reason, changed_by."""
    return (
        f"MERGE INTO {table} t USING (SELECT %(name)s AS name) s ON t.name = s.name "
        f"WHEN MATCHED THEN UPDATE SET value = %(value)s, reason = %(reason)s, "
        f"changed_at = CURRENT_TIMESTAMP(), changed_by = %(changed_by)s "
        f"WHEN NOT MATCHED THEN INSERT (name, value, reason, changed_at, changed_by) "
        f"VALUES (%(name)s, %(value)s, %(reason)s, CURRENT_TIMESTAMP(), %(changed_by)s)"
    )
