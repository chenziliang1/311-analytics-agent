# -*- coding: utf-8 -*-

"""
Encoder tests.

Pure string formatting over hand-built models -- no engine, no reflection, no
database of any kind. Table names come from a made-up ``acme`` / ``widget``
domain on purpose: a test written against this dataset's real table names would
silently become a lie the next time the dataset is swapped.
"""

from agent_app.constants import DbTypeEnum, LLMTypeEnum, TableTypeEnum
from agent_app.db_schema.api import (
    ForeignKeyInfo,
    ColumnInfo,
    TableInfo,
    SchemaInfo,
    DatabaseInfo,
    encode_column_info,
    encode_table_info,
    encode_schema_info,
    encode_database_info,
)


def make_table() -> TableInfo:
    """A table exercising every constraint the encoder can emit."""
    return TableInfo(
        name="widget",
        primary_key=["id"],
        columns=[
            ColumnInfo(name="id", type="INTEGER", llm_type=LLMTypeEnum.INT),
            ColumnInfo(
                name="sku",
                type="VARCHAR(32)",
                llm_type=LLMTypeEnum.STR,
                nullable=False,
                unique=True,
            ),
            ColumnInfo(
                name="part_id",
                type="INTEGER",
                llm_type=LLMTypeEnum.INT,
                nullable=False,
                index=True,
                foreign_keys=[ForeignKeyInfo(name="part.id")],
            ),
            ColumnInfo(
                name="note", type="TEXT", llm_type=LLMTypeEnum.STR, nullable=True
            ),
        ],
    )


def column_of(table: TableInfo, name: str) -> ColumnInfo:
    return next(col for col in table.columns if col.name == name)


class TestEncodeColumnInfo:
    def test_primary_key_absorbs_not_null_and_index(self):
        """A PK is not-null and indexed by definition, so neither is spelled out."""
        table = TableInfo(name="widget", primary_key=["id"])
        column = ColumnInfo(
            name="id",
            type="INTEGER",
            llm_type=LLMTypeEnum.INT,
            nullable=False,
            index=True,
        )
        assert encode_column_info(table, column) == "id:int*PK"

    def test_unique_absorbs_index(self):
        """A unique constraint implies an index, so *IDX is redundant."""
        table = TableInfo(name="widget", primary_key=["id"])
        column = ColumnInfo(
            name="sku",
            type="VARCHAR(32)",
            llm_type=LLMTypeEnum.STR,
            nullable=False,
            unique=True,
            index=True,
        )
        assert encode_column_info(table, column) == "sku:str*UQ*NN"

    def test_not_null_and_index_and_foreign_key(self):
        table = make_table()
        column = column_of(table, "part_id")
        assert encode_column_info(table, column) == "part_id:int*NN*IDX*FK->part.id"

    def test_nullable_column_carries_no_marker(self):
        table = make_table()
        column = column_of(table, "note")
        assert encode_column_info(table, column) == "note:str"

    def test_multiple_foreign_keys_are_concatenated(self):
        table = TableInfo(name="widget", primary_key=["id"])
        column = ColumnInfo(
            name="part_id",
            type="INTEGER",
            llm_type=LLMTypeEnum.INT,
            nullable=True,
            foreign_keys=[
                ForeignKeyInfo(name="part.id"),
                ForeignKeyInfo(name="spare_part.id"),
            ],
        )
        assert (
            encode_column_info(table, column)
            == "part_id:int*FK->part.id*FK->spare_part.id"
        )

    def test_unmapped_type_falls_back_to_dialect_spelling(self):
        """No llm_type means the vocabulary had no entry: show the raw type."""
        table = TableInfo(name="widget", primary_key=["id"])
        column = ColumnInfo(name="geom", type="GEOMETRY", llm_type=None, nullable=True)
        assert encode_column_info(table, column) == "geom:GEOMETRY"


class TestEncodeTableInfo:
    def test_table(self):
        expected = (
            "Table widget(\n"
            "  id:int*PK,\n"
            "  sku:str*UQ*NN,\n"
            "  part_id:int*NN*IDX*FK->part.id,\n"
            "  note:str,\n"
            ")"
        )
        assert encode_table_info(make_table()) == expected

    def test_view_and_materialized_view_are_labelled_differently(self):
        """The label is what tells the agent it cannot write to this relation."""
        table = make_table()
        table.table_type = TableTypeEnum.VIEW
        assert encode_table_info(table).startswith("View widget(")
        table.table_type = TableTypeEnum.MATERIALIZED_VIEW
        assert encode_table_info(table).startswith("MaterializedView widget(")

    def test_table_with_no_columns(self):
        table = TableInfo(name="empty")
        assert encode_table_info(table) == "Table empty(\n\n)"


class TestEncodeSchemaInfo:
    def test_tables_are_indented_one_level(self):
        schema = SchemaInfo(name="acme", tables=[make_table()])
        text = encode_schema_info(schema)
        assert text.startswith("Schema acme(\n  Table widget(\n    id:int*PK,")
        assert text.endswith("\n)")

    def test_unnamed_schema_is_called_default(self):
        """SQLite has no schemas, so the name comes back empty."""
        schema = SchemaInfo(name="", tables=[])
        assert encode_schema_info(schema) == "Schema default(\n\n)"


class TestEncodeDatabaseInfo:
    def test_database_type_prefixes_the_block(self):
        """The agent reads this to know which SQL dialect to write."""
        database = DatabaseInfo(
            name="acme_data",
            db_type=DbTypeEnum.POSTGRESQL,
            schemas=[SchemaInfo(name="acme", tables=[make_table()])],
        )
        text = encode_database_info(database)
        assert text.startswith("postgresql Database acme_data(\n  Schema acme(\n")
        assert "      id:int*PK," in text
        assert text.endswith("\n)")


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.db_schema.encoder",
        preview=False,
    )
