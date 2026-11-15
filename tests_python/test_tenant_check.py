# -*- coding: utf-8 -*-

"""
Asking each mapped role how many tenants it sees. Fictional roles throughout,
and a scripted fetch_rows: SQLite has no roles to switch.
"""

import types

import pytest

from agent_app import cli
from agent_app.tenant_access import RoleDirectory, TenantAccess
from agent_app.tenant_check import CANNOT_TAKE, EVERY_TENANT, NO_TENANT, OK, check_tenant_roles, render


@pytest.fixture
def fetches(monkeypatch):
    """
    Answer the tenant count per role from ``seen``: a number, or an error
    message for a role that cannot be taken. ``None`` is the service role.
    """
    from agent_app import sql_utils
    from agent_app.sql_utils import QueryRows

    seen = {None: 3}
    calls = []

    def fake(engine, sql, max_rows, timeout_seconds=None, role=None, restore_role=None, params=None):
        calls.append((role, restore_role))
        answer = seen[role]
        if isinstance(answer, str):
            return QueryRows([], [], False, error=answer)
        return QueryRows(["n"], [[answer]], False)

    monkeypatch.setattr(sql_utils, "fetch_rows", fake)
    return types.SimpleNamespace(seen=seen, calls=calls)


def outcomes(report):
    return {c.role: (c.outcome, c.tenants, c.problem) for c in report.checks}


def test_a_role_scoped_to_some_tenants_is_ok_and_the_connection_is_restored(fetches):
    fetches.seen["SHOP_PLANT_NORTH"] = 1
    access = TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_PLANT_NORTH"})
    report = check_tenant_roles(None, access)
    assert report.ok and report.service_tenants == 3
    assert outcomes(report) == {"SHOP_PLANT_NORTH": (OK, 1, False)}
    assert fetches.calls == [(None, None), ("SHOP_PLANT_NORTH", "SHOP_RO")]


def test_a_role_the_service_user_cannot_take_is_a_problem_with_the_message(fetches):
    fetches.seen["SHOP_ADMIN"] = "Requested role 'SHOP_ADMIN' is not assigned to the executing user"
    report = check_tenant_roles(None, TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_ADMIN"}))
    assert not report.ok and outcomes(report) == {"SHOP_ADMIN": (CANNOT_TAKE, None, True)}
    assert "not assigned" in report.checks[0].error
    assert "not assigned" in render(report, "SHOP_RO")


def test_a_role_the_policy_maps_to_no_tenant_is_a_problem(fetches):
    """The one that fails silently at run time: no error, no rows."""
    fetches.seen["SHOP_PLANT_SOUTH"] = 0
    report = check_tenant_roles(None, TenantAccess(internal_role="SHOP_RO", default_role="SHOP_PLANT_SOUTH"))
    assert not report.ok and outcomes(report) == {"SHOP_PLANT_SOUTH": (NO_TENANT, 0, True)}
    assert report.checks[0].logins == ["(default)"]


def test_a_role_that_sees_every_tenant_is_a_problem_unless_it_is_the_service_role(fetches):
    fetches.seen["SHOP_ANALYST"] = 3
    access = TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_ANALYST", "bo": "SHOP_RO"})
    report = check_tenant_roles(None, access)
    assert outcomes(report) == {
        "SHOP_ANALYST": (EVERY_TENANT, 3, True),
        "SHOP_RO": (EVERY_TENANT, 3, False),
    }
    assert [c.role for c in report.problems] == ["SHOP_ANALYST"]
    # The service role is asked as itself, with nothing switched.
    assert fetches.calls == [(None, None), ("SHOP_ANALYST", "SHOP_RO"), (None, None)]


def test_each_role_is_asked_once_however_many_logins_share_it(fetches):
    fetches.seen["SHOP_PLANT_NORTH"] = 1
    access = TenantAccess(
        internal_role="SHOP_RO",
        user_roles={"ada": "SHOP_PLANT_NORTH", "cy": "SHOP_PLANT_NORTH"},
        default_role="SHOP_PLANT_NORTH",
    )
    report = check_tenant_roles(None, access)
    assert [c.logins for c in report.checks] == [["ada", "cy", "(default)"]]
    assert len(fetches.calls) == 2


def test_the_roles_in_a_role_table_are_checked_too(fetches):
    fetches.seen["SHOP_PLANT_NORTH"] = 1
    directory = RoleDirectory(lambda: [("ada", "SHOP_PLANT_NORTH")])
    report = check_tenant_roles(None, TenantAccess(internal_role="SHOP_RO", user_roles=directory, configured=True))
    assert outcomes(report) == {"SHOP_PLANT_NORTH": (OK, 1, False)}


def test_a_failed_service_count_fails_the_check_without_asking_any_role(fetches):
    fetches.seen[None] = "warehouse suspended"
    report = check_tenant_roles(None, TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_PLANT_NORTH"}))
    assert not report.ok and report.checks == [] and "warehouse suspended" in report.error
    assert fetches.calls == [(None, None)]


def test_with_scoping_off_nothing_is_asked(fetches):
    report = check_tenant_roles(None, TenantAccess(internal_role="SHOP_RO"))
    assert report.ok and not report.enabled and fetches.calls == []
    assert "scoping is off" in render(report)


class TestCommand:
    """The exit code is what a person or a task reads."""

    @staticmethod
    def configure(monkeypatch, **access):
        config = types.SimpleNamespace(
            tenant_access=TenantAccess(internal_role="SHOP_RO", **access),
            tenant_role_table=None,
            tenant_role_map=access.get("user_roles", {}),
        )
        monkeypatch.setattr("agent_app.one.api.one.config", config, raising=False)
        monkeypatch.setattr("agent_app.one.api.one.engine", None, raising=False)

    def test_scoping_off_exits_zero_and_says_so(self, monkeypatch, fetches, capsys):
        self.configure(monkeypatch)
        with pytest.raises(SystemExit) as exc:
            cli.Cli().check_tenant_roles()
        assert exc.value.code == 0 and fetches.calls == []
        assert "scoping is off" in capsys.readouterr().out

    def test_a_problem_exits_one(self, monkeypatch, fetches):
        fetches.seen["SHOP_PLANT_SOUTH"] = 0
        self.configure(monkeypatch, user_roles={"ada": "SHOP_PLANT_SOUTH"})
        with pytest.raises(SystemExit) as exc:
            cli.Cli().check_tenant_roles()
        assert exc.value.code == 1

    def test_every_role_ok_exits_zero(self, monkeypatch, fetches):
        fetches.seen["SHOP_PLANT_NORTH"] = 1
        self.configure(monkeypatch, user_roles={"ada": "SHOP_PLANT_NORTH"})
        with pytest.raises(SystemExit) as exc:
            cli.Cli().check_tenant_roles()
        assert exc.value.code == 0
