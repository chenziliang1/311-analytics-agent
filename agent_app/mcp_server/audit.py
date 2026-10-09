# -*- coding: utf-8 -*-

"""
One line per query the warehouse tool ran or refused: who, what, and how it
ended. Never the result values -- the log answers "who asked what", and a log
that holds data needs the same protection as the data.
"""

import datetime
import json
import sys
import typing as T
from pathlib import Path


def stderr_audit_log():
    """
    An audit sink that writes one JSON line to stderr. Never raises.

    The deployed form: AgentCore Runtime ships a container's output to
    CloudWatch Logs, where these lines are the doc's query log, searchable with
    Logs Insights. stderr, because over stdio stdout is the MCP protocol.
    """

    def write(record: dict) -> None:
        record = {"audit": "warehouse_query", "at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **record}
        try:
            print(json.dumps(record, default=str), file=sys.stderr, flush=True)
        except Exception:  # pragma: no cover
            pass

    return write


#: The warehouse's audit table. Counts only, never result values.
_QUERY_LOG_INSERT = """
INSERT INTO CITYPULSE_AUDIT.QUERY_LOG
    (log_id, session_id, user_id, asked_at, nl_query, generated_sql,
     row_count, tool_name, status, latency_ms)
VALUES
    (:log_id, :session_id, :user_id, :asked_at, :nl_query, :generated_sql,
     :row_count, :tool_name, :status, :latency_ms)
"""


def warehouse_audit_log(engine, pool=None):
    """
    An audit sink that inserts each record into the warehouse's QUERY_LOG.

    The audit trail: who asked what, which SQL ran, how it
    ended. Written from a background thread, because an audit insert is a
    second round trip to the warehouse and must never make an answer slower
    or fail it.
    """
    import concurrent.futures
    import uuid

    import sqlalchemy as sa

    pool = pool or concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit")
    statement = sa.text(_QUERY_LOG_INSERT)

    def insert(row: dict) -> None:
        try:
            with engine.begin() as conn:
                conn.execute(statement, row)
        except Exception as e:  # pragma: no cover - logged, never raised
            print(f"[Audit] QUERY_LOG insert failed: {type(e).__name__}: {e}", file=sys.stderr)

    def write(record: dict) -> None:
        row = {
            "log_id": str(uuid.uuid4()),
            "session_id": str(record.get("session_id") or "unknown"),
            "user_id": str(record.get("user_id") or "anonymous"),
            "asked_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
            "nl_query": str(record.get("intent", "")),
            "generated_sql": record.get("sql"),
            "row_count": int(record.get("row_count") or 0),
            "tool_name": "warehouse_query",
            "status": str(record.get("status", "unknown")),
            "latency_ms": int(float(record.get("seconds") or 0) * 1000),
        }
        return pool.submit(insert, row)

    return write


def fan_out(*sinks: T.Callable[[dict], None]) -> T.Callable[[dict], None]:
    """One sink that writes every record to each of ``sinks``; one failing never stops the rest."""

    def write(record: dict) -> None:
        for sink in sinks:
            try:
                sink(dict(record))
            except Exception:  # pragma: no cover
                pass

    return write


def jsonl_audit_log(path: Path):
    """An audit sink that appends JSON lines to ``path``. Never raises."""

    def write(record: dict) -> None:
        record = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), **record}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError:  # pragma: no cover
            # A read-only filesystem must not take the query tool down with it.
            pass

    return write
