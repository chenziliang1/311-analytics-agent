# -*- coding: utf-8 -*-

"""
Per-user tenant scope against the real warehouse: the one claim no fixture
can stand in for. Skipped without the warehouse's credentials (conftest).

The agent's own service connection, its own role, then the demo role the
loader maps to one tenant: the row access policy must scope by the role the
query runs as, and the pooled connection must come back as the service role.
"""

import pytest

from agent_app.sql_utils import fetch_rows

TENANTS = "SELECT COUNT(DISTINCT tenant_id) AS n FROM service_requests"
ROLE = "SELECT CURRENT_ROLE() AS r"


@pytest.mark.warehouse
def test_a_query_run_as_the_demo_role_sees_one_tenant_and_the_connection_comes_back():
    from agent_app.api import one

    if one.config.warehouse != "snowflake":
        pytest.skip("role switching is checked on Snowflake")
    engine, own = one.engine, one.config.snowflake_role

    scoped = fetch_rows(engine, TENANTS, 10, role="CITYPULSE_TENANT_DEMO", restore_role=own)
    assert scoped.error is None, scoped.error
    assert scoped.rows == [[1]]

    # The same pool, no role: the service role again, and every tenant.
    assert fetch_rows(engine, ROLE, 10).rows == [[own]]
    assert fetch_rows(engine, TENANTS, 10).rows[0][0] > 1


@pytest.mark.warehouse
def test_a_role_the_user_cannot_take_fails_closed():
    from agent_app.api import one

    if one.config.warehouse != "snowflake":
        pytest.skip("role switching is checked on Snowflake")
    engine, own = one.engine, one.config.snowflake_role
    result = fetch_rows(engine, TENANTS, 10, role="ACCOUNTADMIN", restore_role=own)
    assert result.error and result.rows == []
    assert fetch_rows(engine, ROLE, 10).rows == [[own]]
