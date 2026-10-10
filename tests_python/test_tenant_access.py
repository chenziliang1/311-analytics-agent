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
