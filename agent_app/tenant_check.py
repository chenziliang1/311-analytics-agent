# -*- coding: utf-8 -*-

"""
Whether the roles tenant scope maps users to work in the warehouse.

:mod:`agent_app.tenant_access` checks a role's *name* only, so a mapping that
is wrong in the warehouse surfaces as someone's failed or empty question: a
role the service user cannot take fails at the first query; a role the row
access policy has no row for returns no rows and no error; a role as broad as
the service role sees every tenant, silently. This asks each mapped role the
same question -- how many tenants do you see -- and says which of those it is.

Run by hand (``agent-app check-tenant-roles``), never at startup: it changes
nothing about how the agent scopes a query.
"""

import dataclasses

from . import sql_utils
from .tenant_access import TenantAccess

TENANTS = "SELECT COUNT(DISTINCT tenant_id) AS n FROM service_requests"

CANNOT_TAKE = "cannot be taken"
NO_TENANT = "maps to no tenant"
EVERY_TENANT = "sees every tenant"
OK = "ok"


@dataclasses.dataclass(frozen=True)
class RoleCheck:
    role: str
    #: The logins mapped to it; ``(default)`` for TENANT_DEFAULT_ROLE.
    logins: list[str]
    #: One of CANNOT_TAKE, NO_TENANT, EVERY_TENANT, OK.
    outcome: str
    #: Distinct tenants the role sees, None when it could not be asked.
    tenants: int | None = None
    #: The warehouse's message when the role could not be taken.
    error: str | None = None
    #: Wrong enough to fix. Every tenant is expected of the internal role only.
    problem: bool = False


@dataclasses.dataclass(frozen=True)
class TenantRoleReport:
    #: Per-user scope is configured at all; when not, nothing was asked.
    enabled: bool
    #: Distinct tenants the service role sees, None when it could not be asked.
    service_tenants: int | None = None
    #: Why the service role's own count failed; then no role was asked.
    error: str | None = None
    checks: list[RoleCheck] = dataclasses.field(default_factory=list)

    @property
    def problems(self) -> list[RoleCheck]:
        return [c for c in self.checks if c.problem]

    @property
    def ok(self) -> bool:
        return self.error is None and not self.problems


def _count(engine, **run_as) -> tuple[int | None, str | None]:
    result = sql_utils.fetch_rows(engine, TENANTS, 1, **run_as)
    if result.error:
        return None, result.error
    return (int(result.rows[0][0] or 0) if result.rows else 0), None


def check_tenant_roles(engine, access: TenantAccess) -> TenantRoleReport:
    """
    Ask every role in ``access`` -- each one the map names, and the default --
    how many tenants it sees, and compare it with the service role.

    Each role is asked the way :func:`~agent_app.mcp_server.warehouse.run_as_for`
    would run a user's query: switched to, and back to the internal role after.
    """
    if not access.enabled:
        return TenantRoleReport(enabled=False)
    logins: dict[str, list[str]] = {}
    for login, role in dict(access.user_roles).items():
        logins.setdefault(role, []).append(login)
    if access.default_role is not None:
        logins.setdefault(access.default_role, []).append("(default)")

    service, error = _count(engine)
    if error:
        return TenantRoleReport(enabled=True, error=f"the service role's own count failed: {error}")
    checks = []
    for role, names in logins.items():
        internal = role == access.internal_role
        # The internal role is the connection's own: nothing to switch, as at run time.
        run_as = {} if internal else dict(role=role, restore_role=access.internal_role)
        tenants, error = _count(engine, **run_as)
        if error:
            check = RoleCheck(role, names, CANNOT_TAKE, error=error, problem=True)
        elif tenants == 0:
            check = RoleCheck(role, names, NO_TENANT, tenants=0, problem=True)
        elif tenants == service:
            check = RoleCheck(role, names, EVERY_TENANT, tenants=tenants, problem=not internal)
        else:
            check = RoleCheck(role, names, OK, tenants=tenants)
        checks.append(check)
    return TenantRoleReport(enabled=True, service_tenants=service, checks=checks)


def render(report: TenantRoleReport, internal_role: str | None = None) -> str:
    if not report.enabled:
        return "Tenant scoping is off (no TENANT_ROLE_MAP, TENANT_DEFAULT_ROLE or TENANT_ROLE_TABLE): nothing to check."
    if report.service_tenants is None:
        return f"FAIL  {report.error}"
    who = internal_role or "the connection's own"
    lines = [f"The service role ({who}) sees {report.service_tenants} tenants."]
    for c in report.checks:
        mark = "FAIL" if c.problem else "ok  "
        if c.outcome == CANNOT_TAKE:
            detail = f"{CANNOT_TAKE}: {c.error}"
        elif c.outcome == EVERY_TENANT:
            detail = f"{EVERY_TENANT} ({c.tenants})" + ("; it is the service role" if not c.problem else "")
        elif c.outcome == NO_TENANT:
            detail = f"{NO_TENANT}: the row access policy matches nothing for it (no TENANT_ROLE_ACCESS row?)"
        else:
            detail = f"ok, {c.tenants} tenant{'s' if c.tenants != 1 else ''}"
        lines.append(f"  {mark}  {c.role:28s} {detail}  [{', '.join(c.logins)}]")
    problems = len(report.problems)
    lines.append(f"\n{problems} problem{'s' if problems != 1 else ''}." if problems else "\nEvery role checks out.")
    return "\n".join(lines)
