# -*- coding: utf-8 -*-

"""
Spend caps for a public deployment, backed by DynamoDB counters.

Two caps, both in the same table. The monthly one is one row per
(deployment, month) and bounds what the whole demo can cost. The daily one is
one row per (network address, day) and bounds what one visitor can cost, since
the chat needs no sign-in: :func:`count_request` adds one and refuses past the
cap in a single atomic write.

**Off by default** (``Config.quota_enabled``): it needs a DynamoDB table that
does not exist until someone creates it, and it is meant for a public
deployment, where the person paying is not the person typing.

The entry points check that flag themselves and return early, so a caller never
has to guard the call. Same for infrastructure faults: **metering must never
deny service**, so a DynamoDB failure is logged and swallowed here rather than
in every caller. The one thing that does propagate is
:class:`QuotaExceededError`, which is the answer, not a fault.

Every function takes the ``dynamodb_client`` and the ``config`` explicitly rather
than reaching for the ``one`` singleton, which is what lets a test drive this
with a stub and no AWS. The client is built once in
:meth:`~agent_app.one.one_03_boto3.Boto3Mixin.dynamodb_client`, so this module
never sees a session and cannot reach any other service.
"""

import typing as T
import datetime
import hashlib

from .utils import debug

if T.TYPE_CHECKING:  # pragma: no cover
    from .config import Config


#: Identifies this app in a quota table it may share with other codebases.
#: A constant rather than config: it is a fact about *this repository*, not the
#: dataset it serves, so a dataset swap must not touch it.
APP_ID = "311-analytics-agent"

#: The DynamoDB attribute names. Fixed on purpose -- see the module docstring.
ATTR_INVOKE = "total_invoke"
ATTR_INPUT_TOKEN = "total_input_token"
ATTR_OUTPUT_TOKEN = "total_output_token"
#: The daily per-address rows expire on their own: the table's TTL attribute.
ATTR_TTL = "ttl"

#: How long a per-address row outlives its day before DynamoDB deletes it.
#: Generous on purpose; the key carries the date, so a stale row is never read.
REQUEST_ROW_LIFETIME = datetime.timedelta(days=2)


class QuotaExceededError(RuntimeError):
    """Raised when the current month's usage has hit a configured cap."""


def get_request_id(
    config: "Config", ip: str, kind: str = "chat", now: datetime.datetime | None = None
) -> str:
    """
    The partition key of one network address's counter for one day:
    ``<app>:<dataset>:<kind>:<hash>:<YYYY-MM-DD>``.

    The address is stored hashed, not in clear: the counter needs a stable
    key, not the address, and the table is not a visitor log. ``kind``
    separates what is counted (questions, feedback) so each has its own cap.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    digest = hashlib.sha256(ip.strip().encode()).hexdigest()[:16]
    return f"{APP_ID}:{config.schema_name}:{kind}:{digest}:{now:%Y-%m-%d}"


def count_request(
    dynamodb_client: T.Any,
    config: "Config",
    ip: str,
    kind: str = "chat",
    cap: int | None = None,
    now: datetime.datetime | None = None,
) -> int | None:
    """
    Count one request from ``ip`` and refuse it once today's cap is passed.

    One atomic ``ADD`` that returns the new total, so two requests arriving
    together cannot both squeeze under the cap. The refused request is
    counted too; it costs nothing, and counting it keeps the key hot so a
    visitor hammering the endpoint stays refused.

    Returns the new count, or None when the cap is off, when there is no
    address to count, or when the counter could not be written: none of
    those is a reason to refuse. Raises :class:`QuotaExceededError` when
    the count has gone past the cap.
    """
    if not config.quota_enabled or not ip:
        return None
    if cap is None:
        cap = config.quota_max_requests_per_ip_per_day
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    request_id = get_request_id(config, ip, kind, now)
    expires = int((now + REQUEST_ROW_LIFETIME).timestamp())
    try:
        response = dynamodb_client.update_item(
            TableName=config.dynamodb_table_name_quota,
            Key={"app_id": {"S": request_id}},
            # `ttl` is a reserved word in an expression, hence the alias.
            UpdateExpression=f"ADD {ATTR_INVOKE} :i SET #ttl = :t",
            ExpressionAttributeNames={"#ttl": ATTR_TTL},
            ExpressionAttributeValues={":i": {"N": "1"}, ":t": {"N": str(expires)}},
            ReturnValues="UPDATED_NEW",
        )
        count = _counter(response.get("Attributes") or {}, ATTR_INVOKE)
    except Exception as e:
        debug(f"[quota] request count failed, allowing request: {e}")
        return None
    if count > cap:
        raise QuotaExceededError(f"Daily {kind} cap reached for {request_id}: {count} > {cap}")
    return count


def get_app_id(config: "Config", now: datetime.datetime | None = None) -> str:
    """
    Build the partition key: ``<app>:<dataset>:<YYYY-MM>``.

    A new month is a new row, so usage resets without anyone acting. ``:``
    separates the parts because neither a slug nor a schema name can contain
    one, while both contain the ``-`` and ``_`` that appear inside them.
    """
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    return f"{APP_ID}:{config.schema_name}:{now.year:04d}-{now.month:02d}"


def _counter(item: dict, attr: str) -> int:
    """
    Read one counter out of a raw DynamoDB item.

    The client API returns type-tagged values (``{"N": "42"}``) rather than
    Python numbers, and an attribute that was never written is simply absent.
    Both read as zero.
    """
    return int(item.get(attr, {}).get("N", 0))


def get_usage(
    dynamodb_client: T.Any,
    config: "Config",
    app_id: str | None = None,
) -> dict[str, int]:
    """
    Read the current month's counters.

    A month with no traffic has no row, which is not an error: three zeros, and
    no write is issued.
    """
    if app_id is None:
        app_id = get_app_id(config)
    response = dynamodb_client.get_item(
        TableName=config.dynamodb_table_name_quota,
        Key={"app_id": {"S": app_id}},
    )
    item = response.get("Item") or {}
    return {
        ATTR_INVOKE: _counter(item, ATTR_INVOKE),
        ATTR_INPUT_TOKEN: _counter(item, ATTR_INPUT_TOKEN),
        ATTR_OUTPUT_TOKEN: _counter(item, ATTR_OUTPUT_TOKEN),
    }


def increment_usage(
    dynamodb_client: T.Any,
    config: "Config",
    input_tokens: int,
    output_tokens: int,
    invokes: int = 1,
    app_id: str | None = None,
) -> None:
    """
    Atomically add to the current month's counters. No-op when the cap is off.

    ``ADD`` upserts and treats an absent numeric attribute as 0, which is what
    makes this safe under concurrency: a read-then-write would lose updates
    whenever two requests overlap, and on a public deployment they will.
    """
    if not config.quota_enabled:
        return
    if app_id is None:
        app_id = get_app_id(config)
    try:
        dynamodb_client.update_item(
            TableName=config.dynamodb_table_name_quota,
            Key={"app_id": {"S": app_id}},
            UpdateExpression=(
                f"ADD {ATTR_INVOKE} :i, {ATTR_INPUT_TOKEN} :it, {ATTR_OUTPUT_TOKEN} :ot"
            ),
            # The client API wants numbers as decimal strings under "N".
            ExpressionAttributeValues={
                ":i": {"N": str(int(invokes))},
                ":it": {"N": str(int(input_tokens))},
                ":ot": {"N": str(int(output_tokens))},
            },
        )
    except Exception as e:
        # Losing a count is cheaper than failing a reply the user already paid for.
        debug(f"[quota] increment failed: {e}")


def check_quota(
    dynamodb_client: T.Any,
    config: "Config",
    app_id: str | None = None,
) -> dict[str, int] | None:
    """
    Raise :class:`QuotaExceededError` if any of the three caps is reached.

    Checked before the model is called rather than after, because the point is
    to not spend the tokens.

    Returns the current counters, or None when the cap is off or the counters
    could not be read -- neither is a reason to refuse the request.
    """
    if not config.quota_enabled:
        return None
    if app_id is None:
        app_id = get_app_id(config)
    try:
        usage = get_usage(dynamodb_client, config, app_id)
    except Exception as e:
        # Metering is broken, which is not the user's problem. Note it and let
        # the request through; the alternative is an outage caused by bookkeeping.
        debug(f"[quota] check failed, allowing request: {e}")
        return None
    for attr, cap, label in (
        (ATTR_INVOKE, config.quota_max_invoke_per_month, "invoke"),
        (ATTR_INPUT_TOKEN, config.quota_max_input_token_per_month, "input-token"),
        (ATTR_OUTPUT_TOKEN, config.quota_max_output_token_per_month, "output-token"),
    ):
        if usage[attr] >= cap:
            raise QuotaExceededError(
                f"Monthly {label} cap reached for {app_id}: {usage[attr]} >= {cap}"
            )
    return usage
