# -*- coding: utf-8 -*-

"""
How SQL is executed and handed to the agent.

Pure functions -- ``engine`` is the first parameter, nothing reaches for
``self``. One promise: **a query run through :func:`execute_sql_query` cannot
change the database.**
"""

import dataclasses
import datetime
import decimal
import re
import typing as T

import sqlalchemy as sa
import sqlalchemy.exc as sa_exc
from tabulate import tabulate


def new_sqlite_engine(path: str) -> sa.Engine:
    """
    Open a SQLite database with transactions the caller can actually roll back.

    pysqlite decides whether to open a transaction by sniffing the statement's
    first keyword, and it does not recognise ``WITH`` -- so a CTE-prefixed write
    runs in autocommit and lands on disk with nothing to roll back. Turning
    ``isolation_level`` off and emitting ``BEGIN`` ourselves stops the guessing.

    Lives here rather than with the other engines because it exists to hold up
    :func:`execute_sql_query`'s guarantee, and a safety setting kept away from
    the thing it protects is one the next refactor drops without noticing.
    """
    engine = sa.create_engine(f"sqlite:///{path}")

    @sa.event.listens_for(engine, "connect")
    def _disable_pysqlite_implicit_begin(dbapi_connection, _):
        dbapi_connection.isolation_level = None

    @sa.event.listens_for(engine, "begin")
    def _emit_explicit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    return engine


def format_result(
    result: T.Union["sa.CursorResult", "sa.Result"],
) -> str:
    """
    Format a SQL result as a Markdown table.

    Cheaper than JSON in tokens, closer to what the model saw in training, and
    still readable when a human opens the log.
    """
    records = result.fetchall()
    if len(records) == 0:
        return "No result"

    rows = [list(result.keys())]
    rows.extend(list(record) for record in records)
    return tabulate(rows, headers="firstrow", tablefmt="pipe", floatfmt=".4f")


def ensure_valid_select_query(query: str):
    """
    Reject the obvious non-query before spending a database round trip on it.

    **Not the safety boundary** -- the always-rolled-back transaction in
    :func:`execute_sql_query` is. This only buys the model a fast, readable
    error instead of a driver exception.

    One rule and no keyword denylist on purpose: a denylist cannot tell code
    from data, so it rejects a legal ``WHERE note = 'do not delete'`` while
    still missing what hides inside a legal ``SELECT``. Each false rejection
    costs a model round trip and pushes it toward stranger SQL.
    """
    if not re.match(r"^(SELECT|WITH)\b", query.strip(), re.IGNORECASE):
        raise ValueError(
            "Invalid query: this agent is read-only, start with SELECT or WITH"
        )


@dataclasses.dataclass(frozen=True)
class QueryRows:
    """A query's result as data, for a caller that is code rather than a model."""

    columns: list[str]
    rows: list[list]
    #: The query produced more than ``max_rows``; only the first were kept.
    truncated: bool
    #: The driver's sentence when the query failed, else None.
    error: str | None = None
    #: The database cancelled the query for running past the timeout.
    timed_out: bool = False
    #: The connection failed, not the query: running it again may work.
    transient: bool = False


def _plain(value):
    """JSON-safe: Decimal to float, dates to ISO strings."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def fetch_rows(
    engine: "sa.Engine",
    sql: str,
    max_rows: int,
    timeout_seconds: int | None = None,
) -> QueryRows:
    """
    Run a read-only query and return at most ``max_rows`` rows as plain values.

    The same rolled-back transaction as :func:`execute_sql_query`, so this is
    read-only for the same reason. ``timeout_seconds`` becomes PostgreSQL's
    ``statement_timeout`` for this transaction only; other engines ignore it.
    Never raises.
    """
    try:
        conn = engine.connect()
    except sa_exc.OperationalError as e:
        # Never reached the database: nothing about the query is wrong.
        return QueryRows([], [], False, error=e._message(), transient=True)
    with conn:
        try:
            if timeout_seconds and engine.dialect.name == "postgresql":
                conn.exec_driver_sql(
                    f"SET LOCAL statement_timeout = '{int(timeout_seconds)}s'"
                )
            result = conn.execute(sa.text(sql))
            columns = list(result.keys())
            records = result.fetchmany(max_rows + 1)
        except sa_exc.StatementError as e:
            orig = getattr(e, "orig", None)
            pgcode = getattr(orig, "pgcode", None)
            dialect = engine.dialect.name
            # PostgreSQL: SQLSTATE 57014 is query_canceled, what statement_timeout
            # raises. Snowflake: 604 is a cancelled statement, 630 one past its
            # statement or warehouse timeout.
            timed_out = pgcode == "57014" or (
                dialect == "snowflake" and getattr(orig, "errno", None) in (604, 630)
            )
            # A dropped connection mid-query: SQLSTATE class 08 (connection
            # exception) or 57P (server shutting down), or no code at all when
            # the socket just closed; on Snowflake the connector's
            # OperationalError, which it keeps for network and session faults
            # (a bad query there is a ProgrammingError). Not judged on SQLite,
            # which reports a misspelled column as an OperationalError too.
            transient = getattr(e, "connection_invalidated", False) or (
                isinstance(e, sa_exc.OperationalError)
                and not timed_out
                and (
                    (dialect == "postgresql" and (pgcode is None or pgcode.startswith(("08", "57P"))))
                    or dialect == "snowflake"
                )
            )
            return QueryRows(
                [], [], False, error=e._message(), timed_out=timed_out, transient=transient
            )
        except Exception as e:  # pragma: no cover
            return QueryRows([], [], False, error=str(e))
        finally:
            if conn.in_transaction():
                conn.rollback()
    return QueryRows(
        columns=columns,
        rows=[[_plain(v) for v in r] for r in records[:max_rows]],
        truncated=len(records) > max_rows,
    )


def execute_sql_query(
    engine: "sa.Engine",
    sql: str,
) -> str:
    """
    Execute a read-only query and return the result as a Markdown table.

    The transaction is **always** rolled back, and that -- not the string check
    -- is what makes this read-only: a write that slips past still runs against
    a transaction nobody commits. On SQLite this additionally requires the
    engine from :func:`new_sqlite_engine`.

    **Every failure is returned as text, never raised.** The caller is an LLM
    tool: a traceback ends the turn, a sentence lets it fix its own SQL.
    """
    try:
        ensure_valid_select_query(sql)
    except ValueError as e:
        return f"Error: {e}"

    stmt = sa.text(sql)
    with engine.connect() as conn:
        try:
            result = conn.execute(stmt)
        except sa_exc.StatementError as e:
            # ``_message()`` is private, but it is the only way to get the
            # driver's sentence without the SQL, the bound parameters and the
            # docs URL that ``str(e)`` appends -- a few hundred wasted tokens on
            # every mistyped query. If a SQLAlchemy upgrade removes it, fall
            # back to ``str(e)``; the tool degrades, it does not break.
            return f"Error executing query: {e._message()}"
        except Exception as e:  # pragma: no cover
            return f"Error executing query: {e}"

        try:
            return format_result(result)
        except Exception as e:  # pragma: no cover
            return f"Error formatting result: {e}"
        finally:
            # Written out rather than left to the context manager's implicit
            # rollback: a safety guarantee nobody can see in the code is one the
            # next refactor deletes by adding a commit.
            conn.rollback()
