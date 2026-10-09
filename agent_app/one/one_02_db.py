# -*- coding: utf-8 -*-

"""Database mixin for the One class."""

import typing as T
from functools import cached_property
from pathlib import Path

import sqlalchemy as sa

from ..constants import DbTypeEnum
from ..db_schema.api import new_schema_info
from ..db_schema.api import new_database_info
from ..db_schema.api import encode_database_info
from ..sql_utils import execute_sql_query

if T.TYPE_CHECKING:  # pragma: no cover
    from .one_00_main import One


class DbMixin:
    """Mixin providing database connection and query execution capabilities."""

    def _new_postgres_engine(self: "One", schema_name: str | None = None) -> sa.Engine:
        """
        Open the shared PostgreSQL instance, optionally scoped to one schema.

        When ``schema_name`` is given, every transaction pins ``search_path`` to
        it, which is what lets the agent write ``SELECT ... FROM widget`` and
        have it resolve without ever naming the schema.

        ``None`` leaves the server default -- what the sync needs while it is
        still creating that schema.
        """
        url = sa.URL.create(
            drivername="postgresql+psycopg2",
            username=self.config.db_user,
            password=self.config.db_pass,
            host=self.config.db_host,
            port=self.config.db_port,
            database=self.config.db_name,
        )
        engine = sa.create_engine(url)

        if schema_name:
            # Three ways to pin `search_path`, and only the third one holds.
            #
            # The libpq start-up option (`options=-csearch_path=...`) is the
            # obvious one. A pooled endpoint rejects the *connection*, not the
            # query: Neon's `-pooler` host, and PgBouncer generally, answer with
            # "unsupported startup parameter in options: search_path". It fails
            # inside the driver before anything runs, so it surfaces as the app
            # being unable to reach the database at all.
            #
            # A plain `SET search_path` on the `connect` event is what people try
            # next. It appears to work and is not safe: under transaction pooling
            # the proxy hands out a server connection per *transaction*, so the
            # connection the SET ran on need not be the one a later query gets.
            # That failure looks like a table that exists going missing on load.
            #
            # `SET LOCAL` inside the transaction survives both, because the
            # transaction is the unit the proxy already keeps on one server
            # connection. SQLAlchemy opens one implicitly on first execute, so
            # schema reflection and the agent's SQL tool both get it for free.
            quoted_schema = schema_name.replace('"', '""')

            @sa.event.listens_for(engine, "begin")
            def _pin_search_path(connection):
                connection.exec_driver_sql(
                    f'SET LOCAL search_path TO "{quoted_schema}"'
                )

        return engine

    def _snowflake_private_key(self: "One") -> bytes:
        """The key-pair private key as DER bytes, from PEM text or a file."""
        from cryptography.hazmat.primitives import serialization

        pem = self.config.snowflake_private_key
        if pem:
            # Secrets and environment variables flatten newlines; restore them.
            data = pem.replace("\\n", "\n").encode("utf-8")
        elif self.config.snowflake_private_key_path:
            data = Path(self.config.snowflake_private_key_path).expanduser().read_bytes()
        else:
            raise ValueError(
                "WAREHOUSE is snowflake but neither SNOWFLAKE_PRIVATE_KEY nor "
                "SNOWFLAKE_PRIVATE_KEY_PATH is set."
            )
        key = serialization.load_pem_private_key(data, password=None)
        return key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def _new_snowflake_engine(self: "One") -> sa.Engine:
        """
        The Snowflake warehouse, as the read-only service user.

        Key-pair authentication: no password exists for this user, and nothing
        prompts for MFA. The 45s statement timeout is set per session, under
        the warehouse's own 60s cap, so a slow query comes back as a
        timeout the agent can explain rather than a hung turn.
        """
        c = self.config
        url = sa.URL.create(
            drivername="snowflake",
            username=c.snowflake_user,
            host=c.snowflake_account,
            database=f"{c.snowflake_database}/{c.snowflake_schema}",
            query={"warehouse": c.snowflake_warehouse, "role": c.snowflake_role},
        )
        return sa.create_engine(
            url,
            connect_args={
                "private_key": self._snowflake_private_key(),
                "session_parameters": {
                    "STATEMENT_TIMEOUT_IN_SECONDS": 45,
                    "QUERY_TAG": "citypulse-311-lens",
                },
            },
        )

    @property
    def warehouse_schema(self: "One") -> str:
        """The schema the agent's tables live in, as SQLAlchemy names it."""
        if self.config.warehouse == "snowflake":
            return (self.config.snowflake_schema or "ANALYTICS").lower()
        return self.config.schema_name

    @property
    def sql_dialect(self: "One") -> str:
        """The sqlglot dialect queries must be in to run on the warehouse."""
        return "snowflake" if self.config.warehouse == "snowflake" else "postgres"

    @cached_property
    def remote_postgres_engine(self: "One") -> sa.Engine:
        """
        The shared PostgreSQL instance, unscoped.

        For work that has to reach across schemas -- creating this project's
        schema, inspecting what exists. The agent never uses this one.
        """
        return self._new_postgres_engine()

    @cached_property
    def engine(self: "One") -> sa.Engine:
        """
        The engine everything is served from: our schema in the shared database.

        **The same database in every environment, laptop included.** It used to
        follow the runtime, which meant one prompt and two dialects: SQL examples
        written against SQLite shipped to PostgreSQL, where ``JULIANDAY`` and
        ``strftime`` do not exist. Nothing failed loudly, because the SQL tool
        hands database errors back to the model as text -- the symptom was worse
        answers. The cost of the fix is that local development needs the ``DB_*``
        variables and a completed sync.

        WAREHOUSE=snowflake serves the same tables from Snowflake instead; the
        SQL the agent writes stays PostgreSQL and is transpiled on the way in.
        """
        if self.config.warehouse == "snowflake":
            return self._new_snowflake_engine()
        return self._new_postgres_engine(schema_name=self.config.schema_name)

    @cached_property
    def database_schema_str(self: "One") -> str:
        """
        The database schema, encoded in the compact format the agent reads.

        Scoped to this project's schema, so the agent never sees another
        project's tables in the shared instance.

        An absent schema and an empty one look identical to the reflector, so an
        unsynced database would otherwise hand the agent no tables at all -- it
        would invent plausible ones and answer confidently out of thin air.
        """
        # The schema goes on the MetaData, not only on reflect(): the encoder
        # labels the block with `metadata.schema`, and without it the agent is
        # told it is looking at a schema called "default".
        schema = self.warehouse_schema
        if self.config.warehouse == "snowflake":
            # Three queries instead of reflection's hundred-odd; see
            # db_schema/snowflake.py for why that is a cold-start problem.
            from ..db_schema.snowflake import snowflake_metadata

            metadata = snowflake_metadata(self.engine, self.config.snowflake_database, schema)
        else:
            metadata = sa.MetaData(schema=schema)
            metadata.reflect(bind=self.engine, schema=schema)
        schema_info = new_schema_info(
            engine=self.engine,
            metadata=metadata,
            schema_name=schema,
        )
        database_info = new_database_info(
            # Labelled with the schema name: there is one schema, and a
            # second hand-written name for it was one more thing to keep
            # in step for no reader.
            name=schema,
            # PostgreSQL even on Snowflake: the label tells the SQL writer
            # which dialect to write, and the writer always writes PostgreSQL.
            # The guard transpiles to the warehouse's dialect.
            db_type=DbTypeEnum.POSTGRESQL,
            schemas=[
                schema_info,
            ],
        )
        if not schema_info.tables:  # pragma: no cover - needs an unsynced schema
            raise RuntimeError(
                f"PostgreSQL schema {self.config.schema_name!r} has no tables. "
                f"An absent schema and an empty one look identical to the "
                f"reflector, so this is almost certainly a database that was "
                f"never loaded. Load the dataset into it first."
            )
        return encode_database_info(database_info=database_info)

    def execute_sql_query(self: "One", sql: str) -> str:
        """Execute a SELECT query and return results as a Markdown table."""
        return execute_sql_query(
            engine=self.engine,
            sql=sql,
        )
