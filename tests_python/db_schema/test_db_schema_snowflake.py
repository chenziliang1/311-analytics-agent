# -*- coding: utf-8 -*-

"""
The three-query Snowflake schema read, against a scripted connection.

Fictional tables (widget, plant), per the repo's rule for fixtures.
"""

import contextlib

import sqlalchemy as sa

from agent_app.constants import DbTypeEnum
from agent_app.db_schema.api import encode_database_info, new_database_info, new_schema_info
from agent_app.db_schema.snowflake import snowflake_metadata

COLUMNS = [
    ("PLANT", "PLANT_ID", "NUMBER", 38, 0, "NO"),
    ("PLANT", "NAME", "TEXT", None, None, "NO"),
    ("WIDGET", "WIDGET_ID", "NUMBER", 38, 0, "NO"),
    ("WIDGET", "PLANT_ID", "NUMBER", 38, 0, "YES"),
    ("WIDGET", "WEIGHT", "FLOAT", None, None, "YES"),
    ("WIDGET", "BUILT_AT", "TIMESTAMP_NTZ", None, None, "NO"),
    ("WIDGET", "PRICE", "NUMBER", 10, 2, "YES"),
]
PRIMARY = [{"table_name": "PLANT", "column_name": "PLANT_ID"}, {"table_name": "WIDGET", "column_name": "WIDGET_ID"}]
IMPORTED = [{"fk_table_name": "WIDGET", "fk_column_name": "PLANT_ID", "pk_table_name": "PLANT", "pk_column_name": "PLANT_ID"}]


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def mappings(self):
        return self


class FakeEngine:
    def __init__(self):
        self.queries = []

    @contextlib.contextmanager
    def connect(self):
        yield self

    def exec_driver_sql(self, sql):
        self.queries.append(sql)
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return FakeResult(COLUMNS)
        if "PRIMARY KEYS" in sql:
            return FakeResult(PRIMARY)
        return FakeResult(IMPORTED)


def test_three_queries_and_the_same_encoding_reflection_gives():
    engine = FakeEngine()
    metadata = snowflake_metadata(engine, "acme_db", "analytics")
    assert len(engine.queries) == 3

    # The extractor asks an engine only which relations are views; an empty
    # SQLite with an "analytics" database attached answers "none", as
    # Snowflake would for this schema.
    views = sa.create_engine("sqlite://", poolclass=sa.pool.StaticPool)
    with views.connect() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS analytics")
    schema = new_schema_info(engine=views, metadata=metadata, schema_name="analytics")
    text = encode_database_info(new_database_info(name="analytics", db_type=DbTypeEnum.POSTGRESQL, schemas=[schema]))
    assert "widget_id:int*PK" in text
    assert "plant_id:int*FK->plant.plant_id" in text
    assert "weight:float" in text
    assert "built_at:ts*NN" in text
    assert "price:dec" in text
