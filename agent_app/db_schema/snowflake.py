# -*- coding: utf-8 -*-

"""
Build a schema's SQLAlchemy MetaData from Snowflake in three queries.

SQLAlchemy's reflection asks for each table's columns, primary key, foreign
keys, indexes and constraints separately, and every metadata query on
Snowflake costs a few hundred milliseconds: 40 seconds for 23 tables, paid on
every cold start of the tool server. This reads the same facts in one query
each -- columns, primary keys, imported keys -- and assembles MetaData the
rest of the pipeline (extract -> encode) uses unchanged, so the agent sees
exactly the schema string reflection would have produced.
"""

import sqlalchemy as sa

#: INFORMATION_SCHEMA data_type -> the SQLAlchemy type reflection would give.
#: NUMBER keeps its scale: the extractor reads NUMBER(38,0) as an integer.
_TYPES = {
    "TEXT": lambda p, s: sa.String(),
    "FLOAT": lambda p, s: sa.Float(),
    "DATE": lambda p, s: sa.Date(),
    "TIMESTAMP_NTZ": lambda p, s: sa.TIMESTAMP(),
    "TIMESTAMP_LTZ": lambda p, s: sa.TIMESTAMP(timezone=True),
    "TIMESTAMP_TZ": lambda p, s: sa.TIMESTAMP(timezone=True),
    "BOOLEAN": lambda p, s: sa.Boolean(),
    "NUMBER": lambda p, s: sa.Numeric(int(p or 38), int(s or 0)),
}


def snowflake_metadata(engine: sa.Engine, database: str, schema: str) -> sa.MetaData:
    """
    MetaData for every table in ``database.schema``, names lowercased the way
    SQLAlchemy's Snowflake dialect normalizes them.
    """
    db, sch = database.upper(), schema.upper()
    with engine.connect() as conn:
        columns = conn.exec_driver_sql(
            f"""SELECT table_name, column_name, data_type, numeric_precision,
                       numeric_scale, is_nullable
                FROM {db}.INFORMATION_SCHEMA.COLUMNS
                WHERE table_schema = '{sch}'
                ORDER BY table_name, ordinal_position"""
        ).fetchall()
        primary = conn.exec_driver_sql(f"SHOW PRIMARY KEYS IN SCHEMA {db}.{sch}").mappings().fetchall()
        imported = conn.exec_driver_sql(f"SHOW IMPORTED KEYS IN SCHEMA {db}.{sch}").mappings().fetchall()

    pk = {(r["table_name"].lower(), r["column_name"].lower()) for r in primary}
    fk = {
        (r["fk_table_name"].lower(), r["fk_column_name"].lower()):
            f"{schema.lower()}.{r['pk_table_name'].lower()}.{r['pk_column_name'].lower()}"
        for r in imported
    }

    metadata = sa.MetaData(schema=schema.lower())
    by_table: dict[str, list[sa.Column]] = {}
    for table, column, dtype, precision, scale, nullable in columns:
        t, c = table.lower(), column.lower()
        make = _TYPES.get(dtype)
        args = [sa.ForeignKey(fk[(t, c)])] if (t, c) in fk else []
        by_table.setdefault(t, []).append(
            sa.Column(
                c,
                make(precision, scale) if make else sa.types.NullType(),
                *args,
                primary_key=(t, c) in pk,
                nullable=nullable == "YES" and (t, c) not in pk,
            )
        )
    for table, cols in by_table.items():
        sa.Table(table, metadata, *cols)
    return metadata
