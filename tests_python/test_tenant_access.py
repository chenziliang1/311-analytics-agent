# -*- coding: utf-8 -*-

"""
Who runs as which role. Fictional roles throughout.
"""

import pytest

from agent_app.tenant_access import TenantAccess, check_role_name, parse_role_map


def test_nothing_configured_switches_nothing():
    """A deployment serving internal staff only: everyone is the connection's role."""
    access = TenantAccess(internal_role="SHOP_RO")
    assert not access.enabled
    for user in ("ada", "", None, "anonymous"):
        decision = access.role_for(user)
        assert (decision.role, decision.switch, decision.refused) == (None, False, None)


def test_mapped_user_runs_as_their_role():
    access = TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_PLANT_NORTH"})
    decision = access.role_for("ada")
    assert (decision.role, decision.switch, decision.refused) == ("SHOP_PLANT_NORTH", True, None)


def test_a_user_mapped_to_the_internal_role_needs_no_switch():
    access = TenantAccess(internal_role="SHOP_RO", user_roles={"bo": "SHOP_RO"})
    decision = access.role_for("bo")
    assert (decision.role, decision.switch) == ("SHOP_RO", False)


def test_unmapped_user_gets_the_default_role():
    access = TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_PLANT_NORTH"}, default_role="SHOP_GUEST")
    assert access.role_for("cy").role == "SHOP_GUEST"
    assert access.role_for(None).role == "SHOP_GUEST"


def test_unmapped_user_without_a_default_is_refused():
    """Fail closed: an unknown user is not an internal one."""
    access = TenantAccess(internal_role="SHOP_RO", user_roles={"ada": "SHOP_PLANT_NORTH"})
    for user in ("cy", "", None):
        decision = access.role_for(user)
        assert decision.role is None and decision.refused
    assert "anonymous" in access.role_for(None).refused


def test_a_default_role_alone_enables_scope():
    access = TenantAccess(internal_role="SHOP_RO", default_role="SHOP_GUEST")
    assert access.enabled
    assert access.role_for("anyone").role == "SHOP_GUEST"


class TestRoleDirectory:
    """A map read from somewhere that can change, refreshed on a clock."""

    @staticmethod
    def directory(rows, **kwargs):
        from agent_app.tenant_access import RoleDirectory

        reads = []

        def source():
            reads.append(1)
            result = rows[0]
            if isinstance(result, Exception):
                raise result
            return result

        clock = {"t": 0.0}
        errors = []
        d = RoleDirectory(source, ttl_seconds=300, clock=lambda: clock["t"], on_error=errors.append, **kwargs)
        return d, reads, clock, errors

    def test_reads_once_per_ttl(self):
        rows = [[("ada", "SHOP_PLANT_NORTH")]]
        d, reads, clock, _ = self.directory(rows)
        assert d["ada"] == "SHOP_PLANT_NORTH" and d.get("bo") is None and len(d) == 1
        assert len(reads) == 1
        rows[0] = [("ada", "SHOP_PLANT_NORTH"), ("bo", "SHOP_RO")]
        assert "bo" not in d and len(reads) == 1  # still cached
        clock["t"] = 301
        assert d["bo"] == "SHOP_RO" and len(reads) == 2

    def test_overrides_win_and_count_as_mapped(self):
        d, _, _, _ = self.directory([[("ada", "SHOP_PLANT_NORTH")]], overrides={"ada": "SHOP_RO", "cy": "SHOP_GUEST"})
        assert d["ada"] == "SHOP_RO" and d["cy"] == "SHOP_GUEST" and set(d) == {"ada", "cy"}

    def test_a_failed_read_keeps_the_last_good_map(self):
        rows = [[("ada", "SHOP_PLANT_NORTH")]]
        d, _, clock, errors = self.directory(rows)
        assert d["ada"] == "SHOP_PLANT_NORTH"
        rows[0] = RuntimeError("warehouse down")
        clock["t"] = 301
        assert d["ada"] == "SHOP_PLANT_NORTH" and len(errors) == 1

    def test_before_any_good_read_nobody_is_mapped(self):
        """
        Fail closed: a table that cannot be read maps nobody, and with no
        default role nobody runs a query. The empty map must not pass for
        "no map configured", which would run everyone as the service role.
        """
        rows = [RuntimeError("warehouse down")]
        d, _, clock, errors = self.directory(rows)
        assert len(d) == 0 and errors
        access = TenantAccess(internal_role="SHOP_RO", user_roles=d, configured=True)
        assert access.enabled
        decision = access.role_for("ada")
        assert decision.role is None and decision.switch is False and decision.refused
        with_default = TenantAccess(internal_role="SHOP_RO", user_roles=d, default_role="SHOP_GUEST", configured=True)
        assert with_default.role_for("ada").role == "SHOP_GUEST"
        # Once the table can be read, the same access object maps ada.
        rows[0] = [("ada", "SHOP_PLANT_NORTH")]
        clock["t"] = 301
        assert access.role_for("ada").role == "SHOP_PLANT_NORTH"

    def test_an_unconfigured_empty_map_is_still_no_scope(self):
        """A deployment with no map, table or default serves internal staff only."""
        d, _, _, _ = self.directory([[]])
        assert not TenantAccess(internal_role="SHOP_RO", user_roles=d).enabled

    def test_a_bad_role_name_in_the_table_fails_the_read(self):
        rows = [[("ada", "SHOP RO; DROP")]]
        d, _, _, errors = self.directory(rows)
        assert len(d) == 0 and "not a plain identifier" in str(errors[0])

    def test_refresh_forces_the_next_read(self):
        rows = [[("ada", "SHOP_PLANT_NORTH")]]
        d, reads, _, _ = self.directory(rows)
        d["ada"]
        d.refresh()
        d["ada"]
        assert len(reads) == 2


def test_parse_role_map():
    assert parse_role_map(None) == {}
    assert parse_role_map(" , ") == {}
    assert parse_role_map("ada=SHOP_PLANT_NORTH, Bo = SHOP_RO,") == {"ada": "SHOP_PLANT_NORTH", "Bo": "SHOP_RO"}


@pytest.mark.parametrize("text", ["ada", "=SHOP_RO", "ada=SHOP RO", "ada=SHOP_RO; DROP"])
def test_parse_role_map_rejects(text):
    with pytest.raises(ValueError):
        parse_role_map(text)


@pytest.mark.parametrize("name", ["SHOP_RO", "shop_ro", "_X$1"])
def test_role_names_are_identifiers(name):
    assert check_role_name(name) == name


@pytest.mark.parametrize("name", ["", "1ROLE", "A B", "A;B", "A--", "A\"B", None])
def test_role_names_that_are_not_identifiers_are_refused(name):
    with pytest.raises(ValueError):
        check_role_name(name)
