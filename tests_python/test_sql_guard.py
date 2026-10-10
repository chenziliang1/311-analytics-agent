# -*- coding: utf-8 -*-

"""
The guard against a fictional widget shop, never this dataset's tables.
"""

import pytest

from agent_app.sql_guard import guard_select

TABLES = {"widget", "owner", "part"}
SENSITIVE = {"owner.full_name", "owner.email"}


def guard(sql, max_rows=5000):
    return guard_select(sql, TABLES, SENSITIVE, max_rows=max_rows)


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("SELECT 1; DROP TABLE widget", "exactly one statement"),
        ("UPDATE widget SET id = 1", "only SELECT"),
        ("WITH x AS (DELETE FROM widget RETURNING *) SELECT * FROM x", "write"),
        ("SELECT id FROM widget FOR UPDATE", "write"),
        ("SELECT id INTO copied FROM widget", "write"),
        ("SELECT pg_sleep(10)", "pg_sleep"),
        ("SELECT * FROM public.widget", "without a schema"),
        ("SELECT * FROM secrets", "unknown table 'secrets'"),
        ("SELECT * FROM owner", "personal data"),
        ("SELECT x FROM (SELECT full_name AS x FROM owner) s", "personal data"),
        ("SELECT o.email FROM owner o", "every selected column"),
        ("SELEC id FRM widget", "could not be parsed"),
        ("SELECT SYSTEM$WAIT(10)", "system$wait"),
        ("SELECT GET_DDL('table', 'widget')", "get_ddl"),
        ("SELECT * FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))", "not allowed"),
    ],
)
def test_blocked(sql, reason):
    result = guard(sql)
    assert result.blocked
    assert reason in result.blocked_reason


@pytest.mark.parametrize(
    "sql, repairable",
    [
        ("SELECT * FROM secrets", True),
        ("SELECT * FROM public.widget", True),
        ("SELEC id FRM widget", True),
        ("DELETE FROM widget", False),
        ("SELECT * FROM owner", False),
    ],
)
def test_only_mistakes_are_repairable(sql, repairable):
    assert guard(sql).repairable is repairable


def test_root_personal_data_column_is_removed_with_a_warning():
    result = guard(
        "SELECT w.id, o.full_name, COUNT(*) AS n "
        "FROM widget w JOIN owner o ON o.id = w.owner_id GROUP BY w.id, o.full_name"
    )
    assert not result.blocked
    select_list = result.sql.split("FROM")[0]
    assert "full_name" not in select_list
    assert "o.full_name" in result.warnings[0]
    assert result.tables == {"widget", "owner"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM owner WHERE email IS NOT NULL",
        "SELECT COUNT(*) FROM owner WHERE NOT email IS NULL",
        "SELECT COUNT(*) FROM owner o WHERE o.email IS NULL",
    ],
)
def test_personal_data_null_checks_are_allowed(sql):
    """Counting residents who have an email reads the column but returns none of it."""
    assert not guard(sql).blocked


@pytest.mark.parametrize(
    "sql",
    [
        # Is this resident in here, and what did they report?
        "SELECT w.id FROM widget w JOIN owner o ON o.id = w.owner_id WHERE o.email = 'a@b.com'",
        # A phone number, one prefix at a time.
        "SELECT COUNT(*) FROM owner WHERE full_name LIKE 'Ann%'",
        "SELECT id FROM owner ORDER BY email LIMIT 1",
        "SELECT owner_id FROM owner GROUP BY owner_id HAVING MAX(email) > 'm'",
        "SELECT w.id FROM widget w JOIN owner o ON o.email = w.contact",
        "SELECT id FROM widget WHERE owner_id IN (SELECT id FROM owner WHERE email LIKE '%@corp.com')",
    ],
)
def test_personal_data_cannot_be_compared_sorted_or_joined_on(sql):
    result = guard(sql)
    assert result.blocked
    assert "IS NULL or IS NOT NULL" in result.blocked_reason
    assert not result.repairable


def test_star_over_tables_without_personal_data_is_allowed():
    assert not guard("SELECT * FROM widget").blocked


def test_cte_names_are_not_tables():
    result = guard("WITH recent AS (SELECT id FROM widget) SELECT COUNT(*) FROM recent")
    assert not result.blocked
    assert result.tables == {"widget"}


def test_row_cap_added_when_missing_or_too_large():
    assert "LIMIT 5001" in guard("SELECT id FROM widget").sql
    assert "LIMIT 5001" in guard("SELECT id FROM widget LIMIT 99999").sql


def test_small_limit_is_kept():
    assert "LIMIT 10" in guard("SELECT id FROM widget LIMIT 10").sql


class TestSnowflakeTranspile:
    """Each rewrite is pinned by the PostgreSQL-vs-Snowflake diff that needed it."""

    @staticmethod
    def to_snowflake(sql):
        result = guard_select(sql, TABLES, SENSITIVE, dialect="postgres", target_dialect="snowflake")
        assert not result.blocked
        return result.sql.replace("\n", " ")

    def test_numeric_cast_keeps_its_decimals(self):
        """Unscaled DECIMAL is NUMBER(38,0) on Snowflake: 0.682 became 1."""
        sql = self.to_snowflake("SELECT ROUND((1.0 * id / 3)::numeric, 3) AS r FROM widget")
        assert "DECIMAL(38, 12)" in sql

    def test_timestamp_difference_against_an_interval(self):
        sql = self.to_snowflake("SELECT id FROM widget WHERE shipped_at - built_at <= INTERVAL '30 days'")
        assert "DATEDIFF(second, built_at, shipped_at) <= 2592000" in sql
        sql = self.to_snowflake("SELECT id FROM widget WHERE INTERVAL '24 hours' > (shipped_at - built_at)")
        assert "86400 > DATEDIFF(second, built_at, shipped_at)" in sql

    def test_extract_epoch(self):
        sql = self.to_snowflake("SELECT AVG(EXTRACT(EPOCH FROM (shipped_at - built_at)) / 86400.0) FROM widget")
        assert "DATEDIFF(second, built_at, shipped_at)" in sql
        sql = self.to_snowflake("SELECT EXTRACT(EPOCH FROM built_at) FROM widget")
        assert "DATE_PART(EPOCH_SECOND, built_at)" in sql

    def test_timestamp_plus_interval_is_left_alone(self):
        """Snowflake adds an interval to a timestamp fine; no rewrite needed."""
        assert "+ INTERVAL '60 DAYS'" in self.to_snowflake("SELECT built_at + INTERVAL '60 days' FROM widget")

    def test_same_dialect_is_untouched(self):
        result = guard_select("SELECT ROUND(id::numeric, 3) FROM widget", TABLES, SENSITIVE)
        assert "DECIMAL(38, 12)" not in result.sql


def test_set_operation_is_a_select():
    result = guard("SELECT id FROM widget -- note\nUNION SELECT id FROM part")
    assert not result.blocked
    assert "note" not in result.sql
    assert "LIMIT 5001" in result.sql
