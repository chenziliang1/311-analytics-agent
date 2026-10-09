# -*- coding: utf-8 -*-

"""
Extractor tests.

Reflection is exercised against hand-built ``sa.MetaData`` and an in-memory
SQLite engine. Nothing here connects to PostgreSQL: the extractor only ever
touches SQLAlchemy objects, so a real server would add a dependency without
adding coverage. What *is* PostgreSQL-specific -- the ``search_path`` pinning
and the schema-scoped reflection -- lives in ``one/one_02_db.py``, not here.

Table names come from a made-up ``acme`` / ``widget`` domain on purpose: a test
written against this dataset's real table names would silently become a lie the
next time the dataset is swapped.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from agent_app.constants import DbTypeEnum, LLMTypeEnum, TableTypeEnum
from agent_app.db_schema.api import (
    SQLALCHEMY_TYPE_MAPPING,
    SchemaInfo,
    sqlalchemy_type_to_llm_type,
    new_column_info,
    new_table_info,
    new_schema_info,
    new_database_info,
)


def make_metadata(schema: str | None = None) -> sa.MetaData:
    """Two related tables, built by hand so no server is involved."""
    metadata = sa.MetaData(schema=schema)
    sa.Table(
        "part",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("part_name", sa.String(64), nullable=False),
        schema=schema,
    )
    sa.Table(
        "widget",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "part_id",
            sa.Integer,
            sa.ForeignKey("part.id" if schema is None else f"{schema}.part.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("note", sa.Text, nullable=True),
        schema=schema,
    )
    return metadata


def column_of(table: sa.Table, name: str) -> sa.Column:
    return table.columns[name]


class TestSqlalchemyTypeToLlmType:
    def test_generic_and_dialect_spellings_agree(self):
        """sa.String and sa.VARCHAR have different visit names but one meaning."""
        assert sqlalchemy_type_to_llm_type(sa.String(50)) is LLMTypeEnum.STR
        assert sqlalchemy_type_to_llm_type(sa.VARCHAR(50)) is LLMTypeEnum.STR

    def test_numeric_families(self):
        assert sqlalchemy_type_to_llm_type(sa.Integer()) is LLMTypeEnum.INT
        assert sqlalchemy_type_to_llm_type(sa.BIGINT()) is LLMTypeEnum.INT
        assert sqlalchemy_type_to_llm_type(sa.Float()) is LLMTypeEnum.FLOAT
        assert sqlalchemy_type_to_llm_type(sa.DECIMAL(10, 2)) is LLMTypeEnum.DEC

    def test_temporal_types_stay_distinct(self):
        """DATE, TIME, DATETIME and TIMESTAMP mean different things to a query."""
        assert sqlalchemy_type_to_llm_type(sa.Date()) is LLMTypeEnum.DATE
        assert sqlalchemy_type_to_llm_type(sa.Time()) is LLMTypeEnum.TIME
        assert sqlalchemy_type_to_llm_type(sa.DateTime()) is LLMTypeEnum.DT
        assert sqlalchemy_type_to_llm_type(sa.TIMESTAMP()) is LLMTypeEnum.TS

    def test_unmapped_type_degrades_to_its_own_spelling(self):
        """
        A missing entry is not an error -- the agent gets a longer label.

        ``INET`` is a real PostgreSQL type with no place in the compact
        vocabulary, which is exactly the case this fallback exists for.
        """
        assert "INET" not in SQLALCHEMY_TYPE_MAPPING
        assert sqlalchemy_type_to_llm_type(pg.INET()) == "INET"

    def test_unmapped_type_reaches_the_column_as_its_spelling(self):
        """The model holds only enum values; the spelling travels in `type`."""
        column = sa.Column("addr", pg.INET())
        info = new_column_info(column)
        assert info.llm_type is None
        assert info.type == "INET"

    def test_whole_number_decimal_is_an_integer(self):
        """How Snowflake declares every integer column: NUMBER(38,0)."""
        assert sqlalchemy_type_to_llm_type(sa.Numeric(38, 0)) is LLMTypeEnum.INT
        assert sqlalchemy_type_to_llm_type(sa.Numeric(10, 2)) is LLMTypeEnum.DEC

    def test_postgres_jsonb_is_mapped(self):
        """JSONB has no generic SQLAlchemy type, so it is keyed by name."""
        assert sqlalchemy_type_to_llm_type(pg.JSONB()) is LLMTypeEnum.BLOB


class TestNewColumnInfo:
    def test_plain_column(self):
        table = make_metadata().tables["widget"]
        info = new_column_info(column_of(table, "note"))
        assert info.name == "note"
        assert info.type == "TEXT"
        assert info.llm_type is LLMTypeEnum.STR
        assert info.nullable is True
        assert info.foreign_keys == []

    def test_unique_column(self):
        table = make_metadata().tables["widget"]
        info = new_column_info(column_of(table, "sku"))
        assert info.type == "VARCHAR(32)"
        assert info.nullable is False
        assert info.unique is True

    def test_foreign_key_records_its_target(self):
        table = make_metadata().tables["widget"]
        info = new_column_info(column_of(table, "part_id"))
        assert info.index is True
        assert [fk.name for fk in info.foreign_keys] == ["part.id"]


class TestNewTableInfo:
    def test_primary_key_is_a_list_of_column_names(self):
        table = make_metadata().tables["widget"]
        info = new_table_info(table)
        assert info.name == "widget"
        assert info.table_type is TableTypeEnum.TABLE
        assert info.primary_key == ["id"]
        assert [col.name for col in info.columns] == ["id", "sku", "part_id", "note"]

    def test_table_type_is_caller_supplied(self):
        table = make_metadata().tables["widget"]
        info = new_table_info(table, table_type=TableTypeEnum.VIEW)
        assert info.table_type is TableTypeEnum.VIEW


class TestNewSchemaInfo:
    def test_reflects_every_table_by_default(self):
        engine = sa.create_engine("sqlite://")
        info = new_schema_info(engine=engine, metadata=make_metadata())
        assert info.name == ""
        assert [t.name for t in info.tables] == ["part", "widget"]

    def test_include_pattern_keeps_only_matching_tables(self):
        engine = sa.create_engine("sqlite://")
        info = new_schema_info(
            engine=engine, metadata=make_metadata(), include=["widget*"]
        )
        assert [t.name for t in info.tables] == ["widget"]

    def test_exclude_pattern_hides_a_table_without_dropping_it(self):
        engine = sa.create_engine("sqlite://")
        info = new_schema_info(engine=engine, metadata=make_metadata(), exclude=["part"])
        assert [t.name for t in info.tables] == ["widget"]

    def test_tables_from_another_schema_are_skipped(self):
        """Reflection can pull in more than asked for; only one schema is encoded."""
        engine = sa.create_engine("sqlite://")
        info = new_schema_info(
            engine=engine, metadata=make_metadata(schema="other"), schema_name=None
        )
        assert info.tables == []

    def test_schema_name_comes_from_the_metadata(self):
        """
        The encoder labels the block with ``metadata.schema``, so it has to
        survive the trip. SQLite reaches a named schema through ATTACH, which is
        enough to exercise the path without a PostgreSQL server.
        """
        engine = sa.create_engine("sqlite://")

        @sa.event.listens_for(engine, "connect")
        def _attach_named_schema(dbapi_connection, _):
            dbapi_connection.execute("ATTACH DATABASE ':memory:' AS acme")

        info = new_schema_info(
            engine=engine, metadata=make_metadata(schema="acme"), schema_name="acme"
        )
        assert info.name == "acme"
        assert [t.name for t in info.tables] == ["part", "widget"]

    def test_a_view_is_labelled_as_a_view(self):
        """The inspector, not the metadata, is what knows a relation is a view."""
        engine = sa.create_engine("sqlite://")
        metadata = make_metadata()
        with engine.begin() as conn:
            metadata.create_all(conn)
            conn.exec_driver_sql(
                "CREATE VIEW widget_summary AS SELECT id, sku FROM widget"
            )
        reflected = sa.MetaData()
        reflected.reflect(bind=engine, views=True)
        info = new_schema_info(engine=engine, metadata=reflected)
        by_name = {t.name: t for t in info.tables}
        assert by_name["widget_summary"].table_type is TableTypeEnum.VIEW
        assert by_name["widget"].table_type is TableTypeEnum.TABLE


class TestNewDatabaseInfo:
    def test_wraps_schemas(self):
        schema = SchemaInfo(name="acme", tables=[])
        info = new_database_info(
            name="acme_data", db_type=DbTypeEnum.POSTGRESQL, schemas=[schema]
        )
        assert info.name == "acme_data"
        assert info.db_type is DbTypeEnum.POSTGRESQL
        assert info.schemas == [schema]


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.db_schema.extractor",
        preview=False,
    )
