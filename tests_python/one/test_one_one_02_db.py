# -*- coding: utf-8 -*-

from agent_app.one.one_00_main import one


class TestDbMixin:
    def test_database_schema_str(self):
        _ = one.database_schema_str

    def test_execute_sql_query(self):
        sql = "SELECT 1;"
        _ = one.execute_sql_query(sql)


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.one.one_02_db",
        preview=False,
    )


class TestEngines:
    def test_remote_postgres_engine_is_unscoped(self):
        """
        The sync needs this one rather than `engine`: it creates the schema, so
        it cannot assume there is already one to pin `search_path` to.

        Building an engine opens no connection, so this needs no server.
        """
        assert one.remote_postgres_engine is not one.engine
        assert one.remote_postgres_engine.url.drivername.startswith("postgresql")
