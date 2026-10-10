# -*- coding: utf-8 -*-

"""
Which warehouse role a signed-in user's queries run as.

The warehouse holds every tenant, and its row access policy decides what a
query sees by the session's *role*, not by who is signed in: the agent's
service connection is one identity, cross-tenant by design. Scoping a user to
their tenant therefore means running their query under a role the policy maps
to that tenant, and this is the one place that mapping is decided.

Fail closed. Once a map is configured, a user it does not name gets the
default role, and with no default their queries are refused -- never the
service connection's own, all-seeing role. With nothing configured, every
user is an internal analyst and queries run as the connection's role, as
before: a deployment that serves internal staff only has no mapping to keep.
"""

import dataclasses
import re
import typing as T

#: Warehouse role names, as both Snowflake and PostgreSQL spell unquoted
#: identifiers. Role names come from configuration, never from the model, but
#: they are interpolated into a USE ROLE statement, so they are checked anyway.
ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def check_role_name(name: str, what: str = "role") -> str:
    if not ROLE_NAME.fullmatch(name or ""):
        raise ValueError(f"{what} {name!r} is not a plain identifier (letters, digits, _ and $)")
    return name


def parse_role_map(text: str | None) -> dict[str, str]:
    """
    ``"octocat=CITYPULSE_TENANT_DEMO, alice=CITYPULSE_RO"`` -> ``{"octocat": ..., "alice": ...}``.

    Logins keep their case as the sign-in provider reports them; roles are
    checked as identifiers.
    """
    roles: dict[str, str] = {}
    for entry in (text or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(f"TENANT_ROLE_MAP entry {entry!r} is not login=ROLE")
        login, role = (part.strip() for part in entry.split("=", 1))
        if not login:
            raise ValueError(f"TENANT_ROLE_MAP entry {entry!r} has no login")
        roles[login] = check_role_name(role, "TENANT_ROLE_MAP role")
    return roles


@dataclasses.dataclass(frozen=True)
class TenantAccess:
    #: The service connection's own role: cross-tenant, and what the audit log
    #: and every other write run as. None where the warehouse has no role in
    #: its connection (local PostgreSQL).
    internal_role: str | None
    #: Sign-in login -> the role that user's queries run as.
    user_roles: T.Mapping[str, str] = dataclasses.field(default_factory=dict)
    #: The role for a user the map does not name. None refuses their queries.
    default_role: str | None = None

    @property
    def enabled(self) -> bool:
        """Per-user scope is configured at all."""
        return bool(self.user_roles) or self.default_role is not None

    def role_for(self, user_id: str | None) -> "RoleDecision":
        """The role ``user_id``'s queries run as, or the refusal."""
        if not self.enabled:
            return RoleDecision(role=None, switch=False)
        role = self.user_roles.get(user_id or "", self.default_role)
        if role is None:
            return RoleDecision(
                role=None,
                switch=False,
                refused=f"no tenant access is configured for user {user_id or 'anonymous'!r}",
            )
        # The connection already is the internal role: nothing to switch.
        return RoleDecision(role=role, switch=role != self.internal_role)


@dataclasses.dataclass(frozen=True)
class RoleDecision:
    #: The role the query runs as. None: the connection's own.
    role: str | None
    #: The session must switch to ``role`` before the query runs.
    switch: bool
    #: Why the query may not run at all, else None.
    refused: str | None = None
