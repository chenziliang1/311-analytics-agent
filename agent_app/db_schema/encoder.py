# -*- coding: utf-8 -*-

"""
Encoding: the models in :mod:`.model` -> the compact string the agent is shown.

Pure string formatting, no database access.
"""

import textwrap

from ..constants import LLMColumnConstraintEnum

from .model import (
    ColumnInfo,
    TableInfo,
    SchemaInfo,
    DatabaseInfo,
)

#: Indent unit. A formatting detail of this encoder, not a project-wide constant.
TAB = " " * 2


def encode_column_info(
    table_info: TableInfo,
    column_info: ColumnInfo,
) -> str:
    """
    Encode one column: ``name:type`` plus constraint markers.

    Redundant markers are dropped -- a primary key is already not-null and
    indexed, a unique column is already indexed -- so the agent never reads a
    fact twice.
    """
    col_name = column_info.name
    col_type = column_info.llm_type.value if column_info.llm_type else column_info.type
    pk = (
        f"*{LLMColumnConstraintEnum.PK.value}"
        if column_info.name in table_info.primary_key
        else ""
    )
    uq = f"*{LLMColumnConstraintEnum.UQ.value}" if column_info.unique else ""
    nn = f"*{LLMColumnConstraintEnum.NN.value}" if not column_info.nullable else ""
    idx = f"*{LLMColumnConstraintEnum.IDX.value}" if column_info.index else ""
    # If the column is a primary key, it is not null by default.
    if pk:
        nn = ""
    # If the column is a primary key or unique, by default it is indexed.
    if pk or uq:
        idx = ""
    fk_list = list()
    for fk in column_info.foreign_keys:
        fk_list.append(f"*{LLMColumnConstraintEnum.FK.value}->{fk.name}")
    fk = "".join(fk_list)

    text = f"{col_name}:{col_type}{pk}{uq}{nn}{idx}{fk}"
    return text


def encode_table_info(
    table_info: TableInfo,
) -> str:
    """Encode one table, view or materialized view, one column per line."""
    columns = list()
    for col in table_info.columns:
        col_str = encode_column_info(table_info, col)
        columns.append(f"{TAB}{col_str},")
    columns_def = "\n".join(columns)
    text = f"{table_info.table_type.value} {table_info.name}(\n{columns_def}\n)"
    return text


def encode_schema_info(
    schema_info: SchemaInfo,
) -> str:
    """
    Encode one schema and its tables.

    An unnamed schema is labelled ``default``: SQLite has no schemas, so the
    name comes back empty and the block would otherwise open with nothing.
    """
    tables = list()
    for table in schema_info.tables:
        table_str = encode_table_info(table)
        tables.append(textwrap.indent(table_str, prefix=TAB))
    tables_def = "\n".join(tables)
    if schema_info.name:
        schema_name = schema_info.name
    else:
        schema_name = "default"
    text = f"Schema {schema_name}(\n{tables_def}\n)"
    return text


def encode_database_info(
    database_info: DatabaseInfo,
) -> str:
    """
    Encode the whole database, one line per nesting level.

    The dialect prefixes the outermost block, which is how the agent knows
    which SQL flavour to write.
    """
    schemas = list()
    for schema in database_info.schemas:
        schema_str = encode_schema_info(schema)
        schemas.append(textwrap.indent(schema_str, prefix=TAB))
    schemas_def = "\n".join(schemas)
    text = f"{database_info.db_type.value} Database {database_info.name}(\n{schemas_def}\n)"
    return text
