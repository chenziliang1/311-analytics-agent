# -*- coding: utf-8 -*-

"""
Load the SQLite seed into Snowflake through three layers, secure it,
and check it.

    .venv/bin/python infra/snowflake/load.py            # everything
    .venv/bin/python infra/snowflake/load.py --step verify

    raw     SQLite -> CITYPULSE_311.RAW, as Parquet through an internal stage.
            Types as the seed had them: timestamps are still text here.
    model   STAGING views cast every column to the ANALYTICS type; the 23
            ANALYTICS tables (ddl.sql) are filled from them.
    tenant  The per-request tables (descriptions, events, work orders, ...)
            get a tenant_id copied from their request, so the policy can
            guard them too.
    secure  CITYPULSE_RO gets SELECT on ANALYTICS and INSERT on the query log,
            nothing else; the row access policy goes on every table with a
            tenant_id.
    users   The login -> role table the agent reads for per-user tenant
            scope (CITYPULSE_AUDIT.USER_ROLE_ACCESS), created empty.
    switches The operator's kill switch and per-metric suspensions
            (CITYPULSE_AUDIT.SWITCHES), created with kill off.
    verify  Row counts against the seed, key and foreign-key orphans, RO
            refused a write, the demo role sees one tenant, in the child
            tables as well.

Runs as CITYPULSE_LOADER_SVC with the key bootstrap.py generated. Idempotent:
each step replaces what it builds.
"""

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
#: Where the source SQLite file is looked for. It is 1 GB and not in Git.
SEED_DIR = REPO / "dataset"


def find_seed() -> Path:
    seeds = sorted(SEED_DIR.glob("*.sqlite"))
    if len(seeds) != 1:
        sys.exit(f"Expected exactly one *.sqlite seed in {SEED_DIR}, found {len(seeds)}.")
    return seeds[0]
KEY_DIR = Path.home() / ".snowflake" / "citypulse"

ACCOUNT = os.environ.get("SNOWFLAKE_ACCOUNT", "GOUXAKT-DDC71098")
DATABASE = "CITYPULSE_311"
WAREHOUSE = "CITYPULSE_WH"
POLICY = "ANALYTICS.CROSS_TENANT_INTERNAL"
#: The demo role sees this one tenant, and only through the policy.
DEMO_ROLE, DEMO_TENANT_CODE = "CITYPULSE_TENANT_DEMO", "austin_tx"
CHUNK_ROWS = 500_000

#: ANALYTICS type -> the cast STAGING applies to the RAW column. Strict casts:
#: a value that does not fit fails the load instead of becoming NULL.
CASTS = {
    "NUMBER": "{c}::NUMBER(38,0)",
    "FLOAT": "{c}::FLOAT",
    "TEXT": "{c}::VARCHAR",
    "DATE": "{c}::DATE",
    "TIMESTAMP_NTZ": "{c}::TIMESTAMP_NTZ",
    "BOOLEAN": "{c}::BOOLEAN",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def connect(user: str = "CITYPULSE_LOADER_SVC", key: str = "loader", role: str = "CITYPULSE_LOADER"):
    return snowflake.connector.connect(
        account=ACCOUNT,
        user=user,
        private_key_file=str(KEY_DIR / f"{key}_rsa_key.p8"),
        role=role,
        warehouse=WAREHOUSE,
        database=DATABASE,
    )


def run(cur, sql: str):
    return cur.execute(sql).fetchall()


def seed_tables(seed: sqlite3.Connection) -> list[str]:
    return [r[0] for r in seed.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def step_raw(conn, seed: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for schema in ("RAW", "STAGING", "ANALYTICS", "CITYPULSE_AUDIT"):
        run(cur, f"CREATE SCHEMA IF NOT EXISTS {DATABASE}.{schema}")
    for table in seed_tables(seed):
        started, total = time.time(), 0
        chunks = pd.read_sql_query(
            f"SELECT * FROM {table}", seed, chunksize=CHUNK_ROWS, dtype_backend="pyarrow"
        )
        for i, df in enumerate(chunks):
            df.columns = [c.upper() for c in df.columns]
            ok, _, rows, _ = write_pandas(
                conn, df, table.upper(), database=DATABASE, schema="RAW",
                auto_create_table=True, overwrite=(i == 0), quote_identifiers=False,
            )
            if not ok:
                raise RuntimeError(f"RAW.{table}: write_pandas reported failure")
            total += rows
        log(f"RAW.{table.upper()}: {total:,} rows in {time.time() - started:.0f}s")


def step_model(conn, seed: sqlite3.Connection) -> None:
    cur = conn.cursor()
    ddl = (HERE / "ddl.sql").read_text(encoding="utf-8")
    statements = re.findall(r"CREATE TABLE .*?\n\);", ddl, re.S)
    for stmt in statements:
        run(cur, stmt.replace("CREATE TABLE ", "CREATE OR REPLACE TABLE ", 1))
    log(f"{len(statements)} tables created from ddl.sql")

    # Foreign keys as metadata: Snowflake does not enforce
    # them, but reflection reads them, and the schema the SQL writer is shown
    # marks every join column with its target. Without them the writer saw
    # bare ids on Snowflake where PostgreSQL showed `*FK->tenants.tenant_id`.
    fks = 0
    for table in seed_tables(seed):
        for fk in seed.execute(f"PRAGMA foreign_key_list({table})"):
            parent, child_col, parent_col = fk[2], fk[3], fk[4]
            run(cur, f"ALTER TABLE ANALYTICS.{table.upper()} ADD FOREIGN KEY ({child_col}) "
                     f"REFERENCES ANALYTICS.{parent.upper()} ({parent_col})")
            fks += 1
    log(f"{fks} foreign keys declared")

    columns: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype in run(cur, f"""
        SELECT table_name, column_name, data_type FROM {DATABASE}.INFORMATION_SCHEMA.COLUMNS
        WHERE table_schema = 'ANALYTICS' ORDER BY table_name, ordinal_position"""):
        columns.setdefault(table, []).append((column, dtype))
    # Only the columns the seed has: a derived column (a child table's
    # tenant_id) is in the DDL but not in RAW, and step_tenant fills it.
    raw_columns: dict[str, set[str]] = {}
    for table, column in run(cur, f"""
        SELECT table_name, column_name FROM {DATABASE}.INFORMATION_SCHEMA.COLUMNS
        WHERE table_schema = 'RAW'"""):
        raw_columns.setdefault(table, set()).add(column)

    for table, cols in columns.items():
        cols = [(name, dtype) for name, dtype in cols if name in raw_columns.get(table, set())]
        select = ",\n  ".join(
            CASTS[dtype].format(c=f"raw.{name}") + f" AS {name}" for name, dtype in cols
        )
        run(cur, f"CREATE OR REPLACE VIEW STAGING.{table} AS\nSELECT\n  {select}\nFROM RAW.{table} raw")
        names = ", ".join(name for name, _ in cols)
        started = time.time()
        run(cur, f"INSERT OVERWRITE INTO ANALYTICS.{table} ({names}) SELECT {names} FROM STAGING.{table}")
        log(f"ANALYTICS.{table}: filled from STAGING in {time.time() - started:.0f}s")


#: Per-request tables the seed keys by request_id alone. The row access
#: policy can only guard a table through a tenant_id column, so each gets one,
#: copied from its request (notes: from the work order's request). Without
#: this, a restricted role joining through SERVICE_REQUESTS sees one tenant and
#: reading REQUEST_DESCRIPTIONS directly sees every tenant's resident text.
#: INTAKE_CHANNELS and SERVICE_CATEGORIES are shared vocabulary, not tenant data.
TENANT_VIA_REQUEST = [
    "REQUEST_DESCRIPTIONS", "REQUEST_EVENTS", "REQUEST_ASSIGNMENTS",
    "REQUEST_ATTACHMENTS", "WORK_ORDERS", "MODEL_PREDICTIONS",
]
TENANT_VIA_WORK_ORDER = ["WORK_ORDER_NOTES"]


def step_tenant(conn) -> None:
    """Derive tenant_id on the child tables, so step_secure's policy loop finds them."""
    cur = conn.cursor()
    sources = [(t, "ANALYTICS.SERVICE_REQUESTS", "request_id") for t in TENANT_VIA_REQUEST]
    sources += [(t, "ANALYTICS.WORK_ORDERS", "work_order_id") for t in TENANT_VIA_WORK_ORDER]
    for table, parent, key in sources:
        started = time.time()
        run(cur, f"ALTER TABLE ANALYTICS.{table} ADD COLUMN IF NOT EXISTS tenant_id NUMBER(38,0)")
        run(cur, f"UPDATE ANALYTICS.{table} SET tenant_id = p.tenant_id "
                 f"FROM {parent} p WHERE p.{key} = {table}.{key}")
        # Metadata only (Snowflake does not enforce it), but the schema the SQL
        # writer reads marks the column as a join to TENANTS.
        cur.execute(f"SHOW IMPORTED KEYS IN TABLE ANALYTICS.{table}")
        fk_column = [d[0] for d in cur.description].index("fk_column_name")
        declared = {row[fk_column] for row in cur.fetchall()}
        if "TENANT_ID" not in declared:
            run(cur, f"ALTER TABLE ANALYTICS.{table} ADD FOREIGN KEY (tenant_id) REFERENCES ANALYTICS.TENANTS (tenant_id)")
        missing = run(cur, f"SELECT COUNT(*) FROM ANALYTICS.{table} WHERE tenant_id IS NULL")[0][0]
        log(f"ANALYTICS.{table}: tenant_id derived in {time.time() - started:.0f}s, {missing} rows without one")


def step_secure(conn) -> None:
    cur = conn.cursor()
    for role in ("CITYPULSE_RO", DEMO_ROLE):
        run(cur, f"GRANT USAGE ON DATABASE {DATABASE} TO ROLE {role}")
        run(cur, f"GRANT USAGE ON SCHEMA {DATABASE}.ANALYTICS TO ROLE {role}")
        # ALL, not FUTURE: future grants need MANAGE GRANTS on the account,
        # which the loader deliberately lacks. Every reload replaces the
        # tables and then reruns this step, so ALL covers what exists.
        run(cur, f"GRANT SELECT ON ALL TABLES IN SCHEMA {DATABASE}.ANALYTICS TO ROLE {role}")
    run(cur, f"GRANT USAGE ON SCHEMA {DATABASE}.CITYPULSE_AUDIT TO ROLE CITYPULSE_RO")
    run(cur, f"GRANT SELECT, INSERT ON TABLE {DATABASE}.CITYPULSE_AUDIT.QUERY_LOG TO ROLE CITYPULSE_RO")

    # The feedback table, next to the query log. IF NOT EXISTS, and here
    # rather than in ddl.sql: the model step replaces every table it creates,
    # and a reload must not throw away what users said about the answers.
    run(cur, """CREATE TABLE IF NOT EXISTS CITYPULSE_AUDIT.FEEDBACK (
        feedback_id VARCHAR PRIMARY KEY,
        session_id VARCHAR,
        message_id VARCHAR,
        user_id VARCHAR NOT NULL,
        rated_at TIMESTAMP_NTZ NOT NULL,
        rating VARCHAR NOT NULL,        -- up, down
        comment VARCHAR,
        question VARCHAR,
        answer VARCHAR
    )""")
    run(cur, f"GRANT SELECT, INSERT ON TABLE {DATABASE}.CITYPULSE_AUDIT.FEEDBACK TO ROLE CITYPULSE_RO")
    log("grants: CITYPULSE_RO = SELECT on ANALYTICS + INSERT on QUERY_LOG and FEEDBACK; "
        "demo role = SELECT on ANALYTICS")

    # Which restricted roles may see which tenants. The internal roles see all
    # 120 (cross_tenant_internal); anyone else sees only mapped rows.
    run(cur, "CREATE TABLE IF NOT EXISTS CITYPULSE_AUDIT.TENANT_ROLE_ACCESS (role_name VARCHAR NOT NULL, tenant_id NUMBER(38,0) NOT NULL)")

    # CURRENT_ROLE(), not IS_ROLE_IN_SESSION: with secondary roles on, a
    # session using the demo role still has CITYPULSE_RO "in session", and the
    # policy would hand it every tenant.
    body = f"""CURRENT_ROLE() IN ('CITYPULSE_RO', 'CITYPULSE_LOADER', 'SYSADMIN', 'ACCOUNTADMIN')
        OR EXISTS (
            SELECT 1 FROM {DATABASE}.CITYPULSE_AUDIT.TENANT_ROLE_ACCESS a
            WHERE a.role_name = CURRENT_ROLE() AND a.tenant_id = tenant_id
        )"""
    run(cur, f"CREATE ROW ACCESS POLICY IF NOT EXISTS {POLICY} AS (tenant_id NUMBER) RETURNS BOOLEAN -> {body}")
    run(cur, f"ALTER ROW ACCESS POLICY {POLICY} SET BODY -> {body}")

    # After the policy, not before: reading TENANTS goes through it, and a
    # body still naming the old roles hides every tenant from the loader, so
    # the mapping would come out empty.
    run(cur, "DELETE FROM CITYPULSE_AUDIT.TENANT_ROLE_ACCESS")
    run(cur, f"""INSERT INTO CITYPULSE_AUDIT.TENANT_ROLE_ACCESS
        SELECT '{DEMO_ROLE}', tenant_id FROM ANALYTICS.TENANTS WHERE tenant_code = '{DEMO_TENANT_CODE}'""")

    tables = [t for (t,) in run(cur, f"""
        SELECT table_name FROM {DATABASE}.INFORMATION_SCHEMA.COLUMNS
        WHERE table_schema = 'ANALYTICS' AND column_name = 'TENANT_ID' ORDER BY table_name""")]
    attached = {
        r[0] for r in run(cur, f"""
        SELECT ref_entity_name FROM TABLE({DATABASE}.INFORMATION_SCHEMA.POLICY_REFERENCES(
            policy_name => '{DATABASE}.{POLICY}'))""")
    }
    for table in tables:
        if table not in attached:
            run(cur, f"ALTER TABLE ANALYTICS.{table} ADD ROW ACCESS POLICY {POLICY} ON (tenant_id)")
    log(f"row access policy on {len(tables)} tables with tenant_id")


#: Who runs as which role, one row per signed-in login. The agent reads it
#: (TENANT_ROLE_TABLE) and refreshes every few minutes: adding a customer's
#: user is an INSERT here, not a redeploy. Empty by default: the demo's
#: users are internal.
USER_ROLE_TABLE = f"{DATABASE}.CITYPULSE_AUDIT.USER_ROLE_ACCESS"


def step_users(conn) -> None:
    cur = conn.cursor()
    run(cur, f"CREATE TABLE IF NOT EXISTS {USER_ROLE_TABLE} (login VARCHAR NOT NULL, role_name VARCHAR NOT NULL)")
    run(cur, f"GRANT SELECT ON TABLE {USER_ROLE_TABLE} TO ROLE CITYPULSE_RO")
    rows = run(cur, f"SELECT COUNT(*) FROM {USER_ROLE_TABLE}")[0][0]
    log(f"{USER_ROLE_TABLE}: {rows} login(s) mapped; CITYPULSE_RO may read it")


#: The operator's switches (agent_app/switches.py): kill, suspend:<metric>.
#: The agent reads it every SWITCH_TTL seconds (SWITCH_TABLE); flipping one
#: is `agent-app switch ...`, which writes as the loader.
SWITCH_TABLE = f"{DATABASE}.CITYPULSE_AUDIT.SWITCHES"


def step_switches(conn) -> None:
    cur = conn.cursor()
    run(cur, f"""CREATE TABLE IF NOT EXISTS {SWITCH_TABLE} (
        name VARCHAR NOT NULL,
        value VARCHAR NOT NULL,
        reason VARCHAR,
        changed_at TIMESTAMP_NTZ NOT NULL,
        changed_by VARCHAR
    )""")
    run(cur, f"GRANT SELECT ON TABLE {SWITCH_TABLE} TO ROLE CITYPULSE_RO")
    if run(cur, f"SELECT COUNT(*) FROM {SWITCH_TABLE} WHERE name = 'kill'")[0][0] == 0:
        run(cur, f"INSERT INTO {SWITCH_TABLE} VALUES ('kill', 'off', NULL, CURRENT_TIMESTAMP(), 'load.py')")
    thrown = run(cur, f"SELECT name, reason FROM {SWITCH_TABLE} WHERE LOWER(value) IN ('on', 'true', '1', 'yes')")
    log(f"{SWITCH_TABLE}: {len(thrown)} switch(es) thrown {thrown or ''}; CITYPULSE_RO may read it")


def step_verify(conn, seed: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    ok = True

    def check(passed: bool, msg: str) -> None:
        nonlocal ok
        ok &= passed
        log(f"{'PASS' if passed else 'FAIL'}  {msg}")

    for table in seed_tables(seed):
        expected = seed.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        got = run(cur, f"SELECT COUNT(*) FROM ANALYTICS.{table.upper()}")[0][0]
        check(got == expected, f"ANALYTICS.{table.upper()}: {got:,} rows (seed {expected:,})")

    for table in seed_tables(seed):
        pk = [r[1] for r in seed.execute(f"PRAGMA table_info({table})") if r[5]]
        if pk:
            dupes = run(cur, f"SELECT COUNT(*) - COUNT(DISTINCT {pk[0]}) FROM ANALYTICS.{table.upper()}")[0][0]
            check(dupes == 0, f"{table}.{pk[0]}: no duplicate keys")
        for fk in seed.execute(f"PRAGMA foreign_key_list({table})"):
            parent, child_col, parent_col = fk[2], fk[3], fk[4]
            orphans = run(cur, f"""
                SELECT COUNT(*) FROM ANALYTICS.{table.upper()} c
                LEFT JOIN ANALYTICS.{parent.upper()} p ON c.{child_col} = p.{parent_col}
                WHERE c.{child_col} IS NOT NULL AND p.{parent_col} IS NULL""")[0][0]
            check(orphans == 0, f"{table}.{child_col} -> {parent}: {orphans} orphans")

    ro = connect("CITYPULSE_RO_SVC", "ro", "CITYPULSE_RO")
    rcur = ro.cursor()
    tenants = run(rcur, "SELECT COUNT(DISTINCT tenant_id) FROM ANALYTICS.SERVICE_REQUESTS")[0][0]
    check(tenants == 120, f"CITYPULSE_RO sees {tenants} tenants")
    try:
        rcur.execute("INSERT INTO ANALYTICS.INTAKE_CHANNELS VALUES (99, 'x', 'x', 'x', 0)")
        check(False, "CITYPULSE_RO was able to INSERT into ANALYTICS")
    except snowflake.connector.errors.ProgrammingError:
        check(True, "CITYPULSE_RO refused an INSERT into ANALYTICS")
    rcur.execute(f"USE ROLE {DEMO_ROLE}")
    seen = run(rcur, "SELECT DISTINCT t.tenant_code FROM ANALYTICS.SERVICE_REQUESTS sr JOIN ANALYTICS.TENANTS t ON t.tenant_id = sr.tenant_id")
    check([r[0] for r in seen] == [DEMO_TENANT_CODE], f"{DEMO_ROLE} sees only {seen}")
    # The child tables, read directly rather than through SERVICE_REQUESTS.
    for table in TENANT_VIA_REQUEST + TENANT_VIA_WORK_ORDER:
        tenants = run(rcur, f"SELECT COUNT(DISTINCT tenant_id), COUNT_IF(tenant_id IS NULL) FROM ANALYTICS.{table}")[0]
        check(tuple(tenants) == (1, 0), f"{DEMO_ROLE} sees {tenants[0]} tenant(s) in {table} directly, {tenants[1]} unscoped rows")
    ro.close()
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--step", choices=["all", "raw", "model", "tenant", "secure", "users", "switches", "verify"], default="all")
    step = parser.parse_args().step
    seed_path = find_seed()
    seed = sqlite3.connect(seed_path)
    conn = connect()
    log(f"seed {seed_path.name} -> {ACCOUNT}/{DATABASE}")
    if step in ("all", "raw"):
        step_raw(conn, seed)
    if step in ("all", "model"):
        step_model(conn, seed)
    if step in ("all", "tenant"):
        step_tenant(conn)
    if step in ("all", "secure"):
        step_secure(conn)
    if step in ("all", "users"):
        step_users(conn)
    if step in ("all", "switches"):
        step_switches(conn)
    ok = True
    if step in ("all", "verify"):
        ok = step_verify(conn, seed)
    conn.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
