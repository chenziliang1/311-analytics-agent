# -*- coding: utf-8 -*-

"""
Reflection: SQLAlchemy metadata -> the models in :mod:`.model`.

Pure functions over objects SQLAlchemy hands you. Only :func:`new_schema_info`
needs a live connection, and only to ask which relations are views.
"""

import typing as T

import sqlalchemy as sa
from sqlalchemy.types import TypeEngine

from ..constants import DbTypeEnum, LLMTypeEnum, TableTypeEnum
from ..utils import match

from .model import (
    ForeignKeyInfo,
    ColumnInfo,
    TableInfo,
    SchemaInfo,
    DatabaseInfo,
)

#: SQLAlchemy ``__visit_name__`` -> the compact vocabulary the LLM is shown.
#:
#: Keyed on the visit name rather than the class so that a generic type and its
#: dialect spelling collapse to one entry. Broader than PostgreSQL on purpose:
#: the sync reflects the SQLite seed too, and a missing key costs nothing since
#: the caller falls back to the dialect's own spelling.
SQLALCHEMY_TYPE_MAPPING: dict[str, LLMTypeEnum] = {
    # String type
    sa.String.__visit_name__: LLMTypeEnum.STR,
    sa.Text.__visit_name__: LLMTypeEnum.STR,
    sa.Unicode.__visit_name__: LLMTypeEnum.STR,
    sa.UnicodeText.__visit_name__: LLMTypeEnum.STR,
    sa.VARCHAR.__visit_name__: LLMTypeEnum.STR,
    sa.NVARCHAR.__visit_name__: LLMTypeEnum.STR,
    sa.CHAR.__visit_name__: LLMTypeEnum.STR,
    sa.NCHAR.__visit_name__: LLMTypeEnum.STR,
    sa.TEXT.__visit_name__: LLMTypeEnum.STR,
    sa.CLOB.__visit_name__: LLMTypeEnum.STR,
    # Integer type
    sa.Integer.__visit_name__: LLMTypeEnum.INT,
    sa.SmallInteger.__visit_name__: LLMTypeEnum.INT,
    sa.BigInteger.__visit_name__: LLMTypeEnum.INT,
    sa.INTEGER.__visit_name__: LLMTypeEnum.INT,
    sa.SMALLINT.__visit_name__: LLMTypeEnum.INT,
    sa.BIGINT.__visit_name__: LLMTypeEnum.INT,
    # float type
    sa.Float.__visit_name__: LLMTypeEnum.FLOAT,
    sa.Double.__visit_name__: LLMTypeEnum.FLOAT,
    sa.REAL.__visit_name__: LLMTypeEnum.FLOAT,
    sa.FLOAT.__visit_name__: LLMTypeEnum.FLOAT,
    sa.DOUBLE.__visit_name__: LLMTypeEnum.FLOAT,
    sa.DOUBLE_PRECISION.__visit_name__: LLMTypeEnum.FLOAT,
    # decimal type
    sa.Numeric.__visit_name__: LLMTypeEnum.DEC,
    sa.NUMERIC.__visit_name__: LLMTypeEnum.DEC,
    sa.DECIMAL.__visit_name__: LLMTypeEnum.DEC,
    # datetime
    sa.DateTime.__visit_name__: LLMTypeEnum.DT,
    sa.DATETIME.__visit_name__: LLMTypeEnum.DT,
    sa.TIMESTAMP.__visit_name__: LLMTypeEnum.TS,
    sa.Date.__visit_name__: LLMTypeEnum.DATE,
    sa.DATE.__visit_name__: LLMTypeEnum.DATE,
    sa.Time.__visit_name__: LLMTypeEnum.TIME,
    sa.TIME.__visit_name__: LLMTypeEnum.TIME,
    # binary type
    sa.LargeBinary.__visit_name__: LLMTypeEnum.BLOB,
    sa.BLOB.__visit_name__: LLMTypeEnum.BLOB,
    sa.BINARY.__visit_name__: LLMTypeEnum.BIN,
    sa.VARBINARY.__visit_name__: LLMTypeEnum.BIN,
    # bool type
    sa.Boolean.__visit_name__: LLMTypeEnum.BOOL,
    sa.BOOLEAN.__visit_name__: LLMTypeEnum.BOOL,
    # special types
    sa.Enum.__visit_name__: LLMTypeEnum.STR,  # stored as string
    sa.JSON.__visit_name__: LLMTypeEnum.STR,
    "JSONB": LLMTypeEnum.BLOB,
    # Snowflake: the three timestamp flavours have no generic SQLAlchemy type.
    "TIMESTAMP_NTZ": LLMTypeEnum.TS,
    "TIMESTAMP_LTZ": LLMTypeEnum.TS,
    "TIMESTAMP_TZ": LLMTypeEnum.TS,
    sa.Uuid.__visit_name__: LLMTypeEnum.STR,  # default storage format
    sa.UUID.__visit_name__: LLMTypeEnum.STR,
    sa.Null.__visit_name__: LLMTypeEnum.NULL,
    sa.ARRAY.__visit_name__: LLMTypeEnum.STR,
    sa.TypeDecorator.__visit_name__: LLMTypeEnum.STR,  # PickleType, Interval, Variant
}


def sqlalchemy_type_to_llm_type(type_: TypeEngine) -> T.Union[LLMTypeEnum, str]:
    """
    Compress a SQLAlchemy column type into the LLM vocabulary.

    An unmapped type degrades to the dialect's own spelling rather than to
    nothing, so a schema the vocabulary does not cover is still usable.
    """
    visit_name = getattr(type_, "__visit_name__", None)
    if visit_name is None:  # pragma: no cover
        return str(type_)
    # A whole-number decimal is an integer to anyone writing SQL against it.
    # Snowflake declares every integer column this way (NUMBER(38,0)), and
    # calling ids and counts "dec" would invite pointless casts.
    if isinstance(type_, sa.Numeric) and getattr(type_, "scale", None) == 0:
        return LLMTypeEnum.INT
    return SQLALCHEMY_TYPE_MAPPING.get(visit_name, str(type_))


def new_column_info(column: "sa.Column") -> ColumnInfo:
    """Build a :class:`~.model.ColumnInfo` from a reflected SQLAlchemy column."""
    llm_type = sqlalchemy_type_to_llm_type(column.type)
    return ColumnInfo(
        name=column.name,
        type=str(column.type),
        # An unmapped type comes back as its own spelling, which is not an
        # LLMTypeEnum: leave llm_type empty and the encoder falls back to
        # `type`, the same spelling. Passing the string through failed
        # validation the first time a dialect had a type the map lacked.
        llm_type=llm_type if isinstance(llm_type, LLMTypeEnum) else None,
        nullable=column.nullable,
        index=column.index,
        unique=column.unique,
        foreign_keys=[
            ForeignKeyInfo(name=str(fk.column)) for fk in column.foreign_keys
        ],
    )


def new_table_info(
    table: "sa.Table",
    table_type: TableTypeEnum = TableTypeEnum.TABLE,
) -> TableInfo:
    """Build a :class:`~.model.TableInfo` from a reflected SQLAlchemy table."""
    return TableInfo(
        name=table.name,
        table_type=table_type,
        primary_key=[col.name for col in table.primary_key.columns],
        columns=[new_column_info(column) for column in table.columns],
    )


def new_schema_info(
    engine: "sa.engine.Engine",
    metadata: "sa.MetaData",
    schema_name: T.Optional[str] = None,
    include: T.Optional[list[str]] = None,
    exclude: T.Optional[list[str]] = None,
) -> SchemaInfo:
    """
    Build a :class:`~.model.SchemaInfo` from already-reflected metadata.

    ``include`` / ``exclude`` keep a table out of the agent's view without
    dropping it from the database; see :func:`agent_app.utils.match`.

    ``engine`` is only used to ask the inspector which relations are views --
    the columns come from ``metadata``, which the caller already reflected.
    """
    insp = sa.inspect(engine)
    try:
        view_names = set(insp.get_view_names(schema=schema_name))
    except NotImplementedError:  # pragma: no cover
        view_names = set()
    try:
        materialized_view_names = set(
            insp.get_materialized_view_names(schema=schema_name)
        )
    except NotImplementedError:
        materialized_view_names = set()

    tables = list()
    for table in metadata.sorted_tables:
        # don't include tables from other schemas
        if table.schema != schema_name:
            continue
        # don't include tables that don't match the criteria
        if match(table.name, include or [], exclude or []) is False:
            continue

        if table.name in view_names:
            table_type = TableTypeEnum.VIEW
        elif table.name in materialized_view_names:  # pragma: no cover
            table_type = TableTypeEnum.MATERIALIZED_VIEW
        else:
            table_type = TableTypeEnum.TABLE
        tables.append(new_table_info(table=table, table_type=table_type))

    return SchemaInfo(name=metadata.schema or "", tables=tables)


def new_database_info(
    name: str,
    db_type: DbTypeEnum,
    schemas: list[SchemaInfo],
) -> DatabaseInfo:
    """Build a :class:`~.model.DatabaseInfo` around one or more schemas."""
    return DatabaseInfo(name=name, db_type=db_type, schemas=schemas)
