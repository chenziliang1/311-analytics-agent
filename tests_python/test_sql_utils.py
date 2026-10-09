# -*- coding: utf-8 -*-

"""
SQL execution tests.

Everything runs against in-memory SQLite. That is not a compromise: the module
is dialect-agnostic by design, and SQLite is the one dialect where the read-only
guarantee is *hardest* to hold (see :func:`~agent_app.sql_utils.new_sqlite_engine`),
so it is the honest place to test it.

Table names come from a made-up ``widget`` domain on purpose: a test written
against this dataset's real table names would silently become a lie the next
time the dataset is swapped.
"""

import sqlalchemy as sa

from agent_app.sql_utils import (
    new_sqlite_engine,
    format_result,
    ensure_valid_select_query,
    execute_sql_query,
)

import pytest


@pytest.fixture
def engine() -> sa.Engine:
    """A seeded in-memory database, opened the way the app opens SQLite."""
    eng = new_sqlite_engine(":memory:")
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE widget (id INTEGER PRIMARY KEY, sku TEXT, price REAL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO widget VALUES (1, 'w-001', 9.5), (2, 'w-002', 12.25)"
        )
    return eng


class TestEnsureValidSelectQuery:
    def test_select_and_with_are_accepted(self):
        ensure_valid_select_query("SELECT 1")
        ensure_valid_select_query("  select 1  ")
        ensure_valid_select_query("WITH t AS (SELECT 1) SELECT * FROM t")

    def test_a_write_is_rejected(self):
        with pytest.raises(ValueError, match="read-only"):
            ensure_valid_select_query("UPDATE widget SET sku = 'x'")

    def test_no_keyword_denylist(self):
        """A legal SELECT that merely mentions a write keyword must pass."""
        ensure_valid_select_query("SELECT * FROM widget WHERE sku = 'do not delete'")

    def test_selectfoo_is_not_select(self):
        """The \\b anchor is what stops a prefix match from slipping through."""
        with pytest.raises(ValueError):
            ensure_valid_select_query("SELECTX 1")


class TestFormatResult:
    def test_empty_result(self, engine):
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT * FROM widget WHERE id = 99"))
            assert format_result(result) == "No result"

    def test_markdown_table_with_header(self, engine):
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT id, sku FROM widget ORDER BY id"))
            text = format_result(result)
        lines = text.splitlines()
        assert lines[0].startswith("|") and "id" in lines[0] and "sku" in lines[0]
        assert "w-001" in text and "w-002" in text

    def test_floats_are_fixed_width(self, engine):
        """floatfmt keeps a column of money from turning into ragged repr noise."""
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT price FROM widget ORDER BY id"))
            text = format_result(result)
        assert "9.5000" in text and "12.2500" in text


class TestExecuteSqlQuery:
    def test_select_returns_a_markdown_table(self, engine):
        text = execute_sql_query(engine, "SELECT sku FROM widget ORDER BY id")
        assert "w-001" in text and "|" in text

    def test_empty_result(self, engine):
        assert execute_sql_query(engine, "SELECT 1 WHERE 0") == "No result"

    def test_cte_is_allowed(self, engine):
        text = execute_sql_query(
            engine, "WITH t AS (SELECT sku FROM widget) SELECT * FROM t"
        )
        assert "w-001" in text

    def test_a_write_comes_back_as_text_not_an_exception(self, engine):
        """The caller is an LLM: a sentence it can act on beats a traceback."""
        text = execute_sql_query(engine, "DELETE FROM widget")
        assert text.startswith("Error: ")
        assert "read-only" in text

    def test_broken_sql_comes_back_as_text(self, engine):
        text = execute_sql_query(engine, "SELECT * FROM no_such_table")
        assert text.startswith("Error executing query: ")
        assert "no_such_table" in text

    def test_error_message_omits_the_docs_url(self, engine):
        """``_message()`` instead of ``str(e)``: no SQL, no params, no URL."""
        text = execute_sql_query(engine, "SELECT * FROM no_such_table")
        assert "sqlalche.me" not in text
        assert "[SQL:" not in text

    def test_a_write_smuggled_behind_a_cte_is_rolled_back(self, engine):
        """
        The guarantee this module exists for.

        SQLite accepts ``WITH ... DELETE``, so this passes the string check and
        really executes. It must still leave the table untouched, because the
        transaction is never committed.
        """
        text = execute_sql_query(
            engine, "WITH t AS (SELECT 1) DELETE FROM widget WHERE id = 1"
        )
        assert not text.startswith("Error: ")

        with engine.connect() as conn:
            remaining = conn.execute(sa.text("SELECT COUNT(*) FROM widget")).scalar()
        assert remaining == 2


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.sql_utils",
        preview=False,
    )
