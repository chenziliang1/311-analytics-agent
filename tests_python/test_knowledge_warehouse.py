# -*- coding: utf-8 -*-

"""
The tenant profile source against the real warehouse, as two different
askers. Skipped without the warehouse's credentials (conftest).
"""

import pytest

from agent_app.mcp_server.knowledge import read_source, source_index
from agent_app.mcp_server.warehouse import WarehouseDeps
from agent_app.tenant_access import TenantAccess


def real_deps(**access):
    from agent_app.api import one
    from agent_app.paths import path_enum
    from agent_app.semantic_layer.api import load_registry

    if one.config.warehouse != "snowflake":
        pytest.skip("tenant scope is checked on Snowflake")
    return WarehouseDeps(
        engine=one.engine,
        registry=load_registry(path_enum.dir_semantic_layer),
        schema="",
        allowed_tables=set(),
        writer=lambda request: "",  # never called: no query is written here
        audit=lambda record: None,
        dialect="postgres",
        target_dialect=one.sql_dialect,
        tenant_access=TenantAccess(internal_role=one.config.snowflake_role, **access),
    )


@pytest.mark.warehouse
def test_an_internal_user_sees_every_tenant_and_reads_a_profile():
    deps = real_deps()
    index = source_index(deps, user_id="analyst")
    assert index.status == "ok", index.warning
    tenants = [line for line in index.index.splitlines() if line.startswith("- [tenant/")]
    assert len(tenants) == 120
    doc = read_source(deps, "tenant/austin_tx", user_id="analyst")
    assert doc.status == "ok" and doc.citation == "[KB:tenant/austin_tx]"
    for field in ("- tenant_code: austin_tx", "- size_tier:", "- vocab_drift_level:", "- snapshot_month: 2026-03-01",
                  "- sla_breach_rate:", "- churn_risk_band:"):
        assert field in doc.text, field


@pytest.mark.warehouse
def test_a_restricted_user_sees_and_reads_their_tenant_only():
    deps = real_deps(user_roles={"demo": "CITYPULSE_TENANT_DEMO"})
    index = source_index(deps, user_id="demo")
    assert index.status == "ok", index.warning
    tenants = [line for line in index.index.splitlines() if line.startswith("- [tenant/")]
    assert tenants == ["- [tenant/austin_tx](tenant/austin_tx): " + tenants[0].split(": ", 1)[1]]
    assert read_source(deps, "tenant/austin_tx", user_id="demo").status == "ok"
    assert read_source(deps, "tenant/mesa_az", user_id="demo").status == "not_found"
    # Definitions are not tenant data.
    assert read_source(deps, "metric/sla_breach_rate", user_id="demo").status == "ok"
