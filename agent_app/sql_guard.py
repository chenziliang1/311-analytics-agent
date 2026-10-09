# -*- coding: utf-8 -*-

"""
Decide whether a generated query may run, by reading its syntax tree.

``sql_utils.ensure_valid_select_query`` looks at the first word, which is
enough when the model writing the SQL is the one the user talks to: the
rolled-back transaction is the real boundary and a false rejection only costs
a retry. A query tool that writes its own SQL from free text is a different
threat model -- the text it starts from is the user's -- so this module parses
the query and checks what it *is*, not how it begins:

- exactly one statement
- a SELECT (or a set operation of SELECTs), with no write anywhere inside it
- only tables the warehouse actually has, named bare
- no personal-data column in a result, and no ``*`` that could carry one
- a row cap, so one careless query cannot flood the agent's context

Pure functions with no database access. The rolled-back transaction in
``sql_utils`` stays in place underneath; this is a second wall, not a
replacement for the first.
"""

import dataclasses

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

#: Statements that change something. Any of these anywhere in the tree blocks
#: the query, including inside a CTE (PostgreSQL allows data-modifying CTEs).
_WRITE_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

#: Functions with effects outside the query, or that read the server's files.
_BLOCKED_FUNCTIONS = {
    "pg_sleep",
    "set_config",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "lo_import",
    "lo_export",
    "dblink",
    "dblink_exec",
}


@dataclasses.dataclass(frozen=True)
class GuardResult:
    #: The query to run, regenerated from the checked tree. None when blocked.
    sql: str | None
    blocked_reason: str | None
    #: Changes made to the query that the user should be told about.
    warnings: list[str]
    #: Base tables the query reads, lowercase.
    tables: set[str]
    #: The refusal is a mistake the writer can fix (a typo'd table, a parse
    #: error), not a boundary (a write, personal data). Only these are worth
    #: a second attempt.
    repairable: bool = False

    @property
    def blocked(self) -> bool:
        return self.sql is None


def _blocked(
    reason: str, tables: set[str] | None = None, repairable: bool = False
) -> GuardResult:
    return GuardResult(
        sql=None,
        blocked_reason=reason,
        warnings=[],
        tables=tables or set(),
        repairable=repairable,
    )


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    """Alias (or bare name) -> base table name, lowercase, for every table read."""
    return {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}


def _is_sensitive(
    column: exp.Column,
    aliases: dict[str, str],
    sensitive: set[str],
    tables: set[str],
) -> bool:
    name = column.name.lower()
    if column.table:
        table = aliases.get(column.table.lower())
        return table is not None and f"{table}.{name}" in sensitive
    # Unqualified: sensitive if any table in the query owns a column by that
    # name. Over-blocks a same-named harmless column, never under-blocks.
    return any(f"{table}.{name}" in sensitive for table in tables)


def _cap_rows(tree: exp.Expression, max_rows: int) -> bool:
    """Apply ``LIMIT max_rows + 1`` unless a smaller literal limit is there."""
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression if isinstance(limit, exp.Limit) else limit
        if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows:
            return False
    # One extra row is how the executor tells "exactly max_rows" from "more".
    tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows + 1)))
    return True


_SECONDS_PER = {
    "SECOND": 1, "MINUTE": 60, "HOUR": 3600, "DAY": 86400, "WEEK": 604800,
}
_COMPARISONS = (exp.LT, exp.LTE, exp.GT, exp.GTE, exp.EQ, exp.NEQ)


def _unparen(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _interval_seconds(node: exp.Expression) -> float | None:
    """``INTERVAL '30 days'`` -> 2592000.0; None for anything else."""
    if not isinstance(node, exp.Interval) or node.unit is None:
        return None
    unit = node.unit.name.upper().rstrip("S")
    try:
        seconds = float(node.this.name) * _SECONDS_PER[unit]
    except (KeyError, ValueError, AttributeError):
        return None
    return int(seconds) if seconds.is_integer() else seconds


def _seconds_between(sub: exp.Sub) -> exp.Expression:
    """``a - b`` on timestamps, as Snowflake's whole seconds from b to a."""
    return exp.Anonymous(this="DATEDIFF", expressions=[exp.var("second"), sub.expression, sub.this])


def _snowflake_rewrite(node: exp.Expression) -> exp.Expression:
    # 1. `x::numeric` arrives as DECIMAL with no scale, and Snowflake reads
    #    that as NUMBER(38,0): ROUND(0.682::numeric, 3) came back as 1, and
    #    every rate in a reference query rounded to 0 or 1.
    if isinstance(node, exp.DataType) and node.this == exp.DataType.Type.DECIMAL and not node.expressions:
        return exp.DataType.build("DECIMAL(38, 12)", dialect="snowflake")

    # 2. `a - b <= INTERVAL '30 days'`: Snowflake subtracts timestamps into an
    #    interval it then cannot compare with an interval literal. Compare
    #    whole seconds instead.
    if isinstance(node, _COMPARISONS):
        left, right = _unparen(node.this), _unparen(node.expression)
        for sub, interval, sub_first in ((left, right, True), (right, left, False)):
            seconds = _interval_seconds(interval)
            if isinstance(sub, exp.Sub) and seconds is not None:
                diff, bound = _seconds_between(sub), exp.Literal.number(seconds)
                return node.__class__(this=diff, expression=bound) if sub_first else node.__class__(this=bound, expression=diff)

    # 3. EXTRACT(EPOCH FROM ...): Snowflake has no EPOCH part. Of a timestamp
    #    difference it is the seconds between them; of a timestamp, its
    #    EPOCH_SECOND.
    if isinstance(node, exp.Extract) and node.this.name.upper() == "EPOCH":
        inner = _unparen(node.expression)
        if isinstance(inner, exp.Sub):
            return _seconds_between(inner)
        return exp.Anonymous(this="DATE_PART", expressions=[exp.var("EPOCH_SECOND"), node.expression])
    return node


def to_warehouse_dialect(tree: exp.Expression, target: str) -> exp.Expression:
    """
    Rewrite what sqlglot's own transpiler gets wrong for ``target``.

    Each rewrite is here because a reference query (the golden set, every
    semantic-layer metric) returned a different answer on the warehouse than
    on PostgreSQL. A rewrite with no such failure behind it would be a guess.
    """
    if target == "snowflake":
        return tree.transform(_snowflake_rewrite)
    return tree


def transpile_sql(sql: str, target: str, source: str = "postgres") -> str:
    """
    Trusted SQL (a reference query, a compiled metric) in ``target``'s dialect.

    The same rewrites the guard applies to the writer's queries, so a metric
    replayed or a golden reference graded on the warehouse is measured with
    exactly the translation the agent's own queries get.
    """
    if target == source:
        return sql
    tree = sqlglot.parse_one(sql, read=source)
    return to_warehouse_dialect(tree, target).sql(dialect=target, pretty=True)


def guard_select(
    sql: str,
    allowed_tables: set[str],
    sensitive_columns: set[str],
    max_rows: int = 5000,
    dialect: str = "postgres",
    target_dialect: str | None = None,
) -> GuardResult:
    """
    Check ``sql`` and return the version that may run, or the reason it may not.

    :param allowed_tables: Base table names the query may read, lowercase.
    :param sensitive_columns: ``table.column`` personal-data columns, lowercase.
    :param max_rows: The executor fetches at most this many rows; the query is
        given a ``LIMIT`` of one more so truncation can be detected.
    :param dialect: The dialect ``sql`` is written in.
    :param target_dialect: The dialect to emit, when the warehouse speaks
        another one. The checks run on the parsed tree, so they hold whatever
        the output dialect; only the final rendering changes.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        return _blocked(
            f"the query could not be parsed: {e.errors[0]['description'] if e.errors else e}",
            repairable=True,
        )
    if len(statements) != 1:
        return _blocked(f"exactly one statement is allowed, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return _blocked(f"only SELECT queries are allowed, got {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, _WRITE_NODES):
            return _blocked(f"the query contains a write ({node.key.upper()})")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _BLOCKED_FUNCTIONS:
                return _blocked(f"function {name} is not allowed")

    ctes = _cte_names(tree)
    tables = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in ctes and not table.db:
            continue
        if table.db or table.catalog:
            return _blocked(
                f"name tables bare, without a schema: {table.sql(dialect=dialect)}",
                repairable=True,
            )
        if name not in allowed_tables:
            # Usually a CTE the query forgot to define, or a misspelling.
            return _blocked(f"unknown table {name!r}", tables, repairable=True)
        tables.add(name)

    aliases = _alias_map(tree)
    sensitive_tables = {c.split(".")[0] for c in sensitive_columns}
    warnings = []
    for select in tree.find_all(exp.Select):
        # Only the outermost SELECT's columns can be dropped without changing
        # what the rest of the query means; anywhere else, refuse instead.
        is_root = select is tree
        kept = []
        for projection in select.expressions:
            if isinstance(projection, exp.Star) or (
                isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
            ):
                if tables & sensitive_tables:
                    return _blocked(
                        "SELECT * over a table holding personal data; list the columns instead",
                        tables,
                    )
                kept.append(projection)
                continue
            leaked = [
                c.sql(dialect=dialect)
                for c in projection.find_all(exp.Column)
                if _is_sensitive(c, aliases, sensitive_columns, tables)
            ]
            if not leaked:
                kept.append(projection)
            elif is_root:
                warnings.append(
                    f"Removed {', '.join(leaked)} from the result: residents' personal data is not shown."
                )
            else:
                return _blocked(
                    f"personal data ({', '.join(leaked)}) cannot be selected", tables
                )
        if not kept:
            return _blocked("every selected column was personal data", tables)
        select.set("expressions", kept)

    _cap_rows(tree, max_rows)
    target = target_dialect or dialect
    if target != dialect:
        tree = to_warehouse_dialect(tree, target)
    return GuardResult(
        sql=tree.sql(dialect=target, pretty=True, comments=False),
        blocked_reason=None,
        warnings=warnings,
        tables=tables,
    )
