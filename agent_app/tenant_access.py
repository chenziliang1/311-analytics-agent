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
import time
import typing as T

#: Warehouse role names, as both Snowflake and PostgreSQL spell unquoted
#: identifiers. Role names come from configuration, never from the model, but
#: they are interpolated into a USE ROLE statement, so they are checked anyway.
ROLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def check_role_name(name: str, what: str = "role") -> str:
    if not ROLE_NAME.fullmatch(name or ""):
        raise ValueError(f"{what} {name!r} is not a plain identifier (letters, digits, _ and $)")
    return name


#: A table name as ``schema.table`` or ``db.schema.table``, each part a plain
#: identifier: it is interpolated into the SELECT that reads the role table.
TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


def check_table_name(name: str, what: str = "table") -> str:
    if not TABLE_NAME.fullmatch(name or ""):
        raise ValueError(f"{what} {name!r} is not a plain table name (schema.table)")
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


class RoleDirectory(T.Mapping[str, str]):
    """
    Login -> role, read from somewhere that can change (a warehouse table)
    and refreshed every ``ttl_seconds``, with a static map laid over it.

    A customer is added by one row in the table, not by a redeploy. When the
    source fails, the last good reading stays in force rather than every
    user becoming unmapped at once; before any good reading, nobody is
    mapped, which with no default role means nobody runs a query: fail
    closed, loudly. That last part holds only because the TenantAccess
    holding this directory is marked ``configured``: an empty directory on
    its own looks like no map at all.
    """

    def __init__(
        self,
        source: T.Callable[[], T.Iterable[tuple[str, str]]],
        overrides: T.Mapping[str, str] | None = None,
        ttl_seconds: float = 300.0,
        clock: T.Callable[[], float] = time.monotonic,
        on_error: T.Callable[[Exception], None] | None = None,
    ):
        self._source = source
        self._overrides = dict(overrides or {})
        self._ttl = ttl_seconds
        self._clock = clock
        self._on_error = on_error
        self._loaded: dict[str, str] = {}
        self._read_at: float | None = None

    def _current(self) -> dict[str, str]:
        now = self._clock()
        if self._read_at is None or now - self._read_at >= self._ttl:
            try:
                rows = {}
                for login, role in self._source():
                    rows[str(login)] = check_role_name(str(role), "role table role")
                self._loaded = rows
            except Exception as e:
                if self._on_error is not None:
                    self._on_error(e)
            self._read_at = now
        return {**self._loaded, **self._overrides}

    def __getitem__(self, login: str) -> str:
        return self._current()[login]

    def __iter__(self):
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())

    def refresh(self) -> None:
        """Read the source again on the next lookup."""
        self._read_at = None


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
    #: Per-user scope was configured, whatever the map holds right now. Set
    #: when a role table is named: a table that cannot be read yet (or is
    #: empty) leaves ``user_roles`` empty, and an empty map must not read as
    #: "nothing configured, everyone is internal", or the one failure the
    #: directory is built to survive would hand every user the service
    #: connection's cross-tenant role.
    configured: bool = False

    @property
    def enabled(self) -> bool:
        """Per-user scope is configured at all."""
        return self.configured or bool(self.user_roles) or self.default_role is not None

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
