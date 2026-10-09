# -*- coding: utf-8 -*-

"""
Tests for the monthly spend cap.

Driven against **moto**, an in-process DynamoDB, rather than hand-written stubs.
The difference matters here: this module speaks the low-level client API, where
numbers travel as ``{"N": "42"}`` and the counters are updated with an ``ADD``
expression. A stub asserts what the author *believed* those look like, so a
wrong belief makes the code and the stub wrong together and the test still
passes. moto runs the real thing, so the type descriptors and the upsert
semantics are checked rather than assumed.

No network: moto intercepts botocore, and the fixture installs dummy credentials
so a misconfiguration cannot reach a real account.
"""

import datetime

import boto3
import pytest
from moto import mock_aws

from agent_app.config import Config
from agent_app.quota import ATTR_INPUT_TOKEN
from agent_app.quota import ATTR_INVOKE
from agent_app.quota import ATTR_OUTPUT_TOKEN
from agent_app.quota import QuotaExceededError
from agent_app.quota import APP_ID
from agent_app.quota import check_quota
from agent_app.quota import get_app_id
from agent_app.quota import get_usage
from agent_app.quota import increment_usage

REGION = "us-east-1"
TABLE = "quota-table"


def make_config(**overrides) -> Config:
    """The real Config, so its validation is exercised too."""
    values = dict(
        schema_name="acme_widgets",
        quota_enabled=True,
        dynamodb_table_name_quota=TABLE,
        quota_max_invoke_per_month=100,
        quota_max_input_token_per_month=1_000,
        quota_max_output_token_per_month=500,
    )
    values.update(overrides)
    return Config(**values)


@pytest.fixture
def dynamodb(monkeypatch):
    """An empty quota table, keyed the way the module documents it needs."""
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)

    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "app_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "app_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


class TestAppId:
    def test_shape_is_app_dataset_and_month(self):
        """
        Three colon-separated parts, so one table can serve many deployments and
        a human reading the console can tell which row is whose.
        """
        moment = datetime.datetime(2026, 3, 9, tzinfo=datetime.timezone.utc)
        app, dataset, month = get_app_id(make_config(), moment).split(":")
        assert app == APP_ID
        assert dataset == "acme_widgets"
        assert month == "2026-03"

    def test_month_rolls_over(self):
        """A new month is a new row, so usage resets without anyone acting."""
        config = make_config()
        jan = get_app_id(config, datetime.datetime(2026, 1, 31, tzinfo=datetime.timezone.utc))
        feb = get_app_id(config, datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc))
        assert jan != feb


class TestGetUsage:
    def test_missing_row_reads_as_zero(self, dynamodb):
        """A month with no traffic has no row. That is not an error."""
        usage = get_usage(dynamodb, make_config(), app_id="x")
        assert usage == {ATTR_INVOKE: 0, ATTR_INPUT_TOKEN: 0, ATTR_OUTPUT_TOKEN: 0}

    def test_tagged_numbers_come_back_as_int(self, dynamodb):
        """
        DynamoDB returns ``{"N": "7"}`` -- a string under a type tag -- and the
        caps are ints. Comparing a str to an int raises.
        """
        dynamodb.put_item(
            TableName=TABLE,
            Item={"app_id": {"S": "x"}, ATTR_INVOKE: {"N": "7"}, ATTR_INPUT_TOKEN: {"N": "12"}},
        )
        usage = get_usage(dynamodb, make_config(), app_id="x")
        assert usage[ATTR_INVOKE] == 7
        assert isinstance(usage[ATTR_INVOKE], int)
        assert usage[ATTR_INPUT_TOKEN] == 12
        # An attribute the row never had reads as zero, not as missing.
        assert usage[ATTR_OUTPUT_TOKEN] == 0

    def test_app_id_defaults_to_this_month(self, dynamodb):
        """The production path: nobody passes app_id, it comes from the clock."""
        config = make_config()
        increment_usage(dynamodb, config, input_tokens=5, output_tokens=2)
        assert get_usage(dynamodb, config)[ATTR_INVOKE] == 1


class TestIncrementUsage:
    def test_creates_the_row_on_first_write(self, dynamodb):
        """``ADD`` upserts: there is no row to create first."""
        config = make_config()
        increment_usage(dynamodb, config, input_tokens=30, output_tokens=8, app_id="x")
        assert get_usage(dynamodb, config, app_id="x") == {
            ATTR_INVOKE: 1,
            ATTR_INPUT_TOKEN: 30,
            ATTR_OUTPUT_TOKEN: 8,
        }

    def test_counters_accumulate(self, dynamodb):
        """
        The reason for ``ADD`` rather than read-then-write: overlapping requests
        must not lose updates, and on a public deployment they will overlap.
        This is the assertion a hand-written stub cannot make -- it would only
        record the call, not apply it.
        """
        config = make_config()
        for _ in range(3):
            increment_usage(dynamodb, config, input_tokens=10, output_tokens=4, app_id="x")
        assert get_usage(dynamodb, config, app_id="x") == {
            ATTR_INVOKE: 3,
            ATTR_INPUT_TOKEN: 30,
            ATTR_OUTPUT_TOKEN: 12,
        }

    def test_invokes_is_settable(self, dynamodb):
        config = make_config()
        increment_usage(dynamodb, config, input_tokens=0, output_tokens=0, invokes=5, app_id="x")
        assert get_usage(dynamodb, config, app_id="x")[ATTR_INVOKE] == 5

    def test_months_do_not_bleed_into_each_other(self, dynamodb):
        """Different app_id, different row -- that is what makes the reset free."""
        config = make_config()
        increment_usage(dynamodb, config, input_tokens=10, output_tokens=1, app_id="2026-01")
        increment_usage(dynamodb, config, input_tokens=99, output_tokens=1, app_id="2026-02")
        assert get_usage(dynamodb, config, app_id="2026-01")[ATTR_INPUT_TOKEN] == 10


class TestCheckQuota:
    def test_under_every_cap_passes(self, dynamodb):
        config = make_config()
        increment_usage(dynamodb, config, input_tokens=999, output_tokens=499, invokes=98, app_id="x")
        assert check_quota(dynamodb, config, app_id="x")[ATTR_INVOKE] == 98

    @pytest.mark.parametrize(
        "kwargs, expected",
        [
            ({"invokes": 100, "input_tokens": 0, "output_tokens": 0}, "invoke"),
            ({"invokes": 0, "input_tokens": 1_000, "output_tokens": 0}, "input-token"),
            ({"invokes": 0, "input_tokens": 0, "output_tokens": 500}, "output-token"),
        ],
    )
    def test_any_cap_reached_blocks(self, dynamodb, kwargs, expected):
        """Each of the three caps blocks on its own, and says which one it was."""
        config = make_config()
        increment_usage(dynamodb, config, app_id="x", **kwargs)
        with pytest.raises(QuotaExceededError, match=expected):
            check_quota(dynamodb, config, app_id="x")

    def test_app_id_defaults_to_this_month(self, dynamodb):
        """The production path: api/index.py passes no app_id."""
        config = make_config()
        increment_usage(dynamodb, config, invokes=100, input_tokens=0, output_tokens=0)
        with pytest.raises(QuotaExceededError, match=get_app_id(config)):
            check_quota(dynamodb, config)

    def test_boundary_is_inclusive(self, dynamodb):
        """At exactly the cap the budget is spent, so the next call must not run."""
        config = make_config()
        increment_usage(dynamodb, config, input_tokens=0, output_tokens=0, invokes=99, app_id="x")
        check_quota(dynamodb, config, app_id="x")
        increment_usage(dynamodb, config, input_tokens=0, output_tokens=0, app_id="x")
        with pytest.raises(QuotaExceededError):
            check_quota(dynamodb, config, app_id="x")


class TestDisabled:
    """
    The flag lives with the feature, not with the caller.

    api/index.py calls both entry points unguarded, so "off" has to mean "does
    nothing and touches no AWS" rather than "the caller remembered an if".
    """

    def test_shipped_config_has_the_cap_disabled(self):
        """Someone cloning the repo must not need a DynamoDB table to chat."""
        assert Config().quota_enabled is False

    def test_check_is_a_no_op(self, dynamodb):
        config = make_config(quota_enabled=False)
        # Way over every cap, and still allowed, because metering is off.
        increment_usage(dynamodb, make_config(), invokes=10_000,
                        input_tokens=0, output_tokens=0, app_id="x")
        assert check_quota(dynamodb, config, app_id="x") is None

    def test_increment_writes_nothing(self, dynamodb):
        config = make_config(quota_enabled=False)
        increment_usage(dynamodb, config, input_tokens=30, output_tokens=8, app_id="x")
        assert dynamodb.get_item(TableName=TABLE, Key={"app_id": {"S": "x"}}).get("Item") is None


class TestMeteringFaultsDoNotDenyService:
    """
    A broken counter is cheaper than an outage, so infrastructure faults are
    logged and swallowed here rather than in each caller.

    The fault is a real one -- a table that does not exist -- so botocore raises
    the exception it actually raises, not one a stub was told to.
    """

    def test_a_failed_check_lets_the_request_through(self, dynamodb, capsys):
        config = make_config(dynamodb_table_name_quota="no-such-table")
        assert check_quota(dynamodb, config, app_id="x") is None
        assert "ResourceNotFound" in capsys.readouterr().err

    def test_a_failed_increment_does_not_raise(self, dynamodb, capsys):
        config = make_config(dynamodb_table_name_quota="no-such-table")
        increment_usage(dynamodb, config, input_tokens=1, output_tokens=1, app_id="x")
        assert "ResourceNotFound" in capsys.readouterr().err

    def test_an_actual_refusal_still_propagates(self, dynamodb):
        """The one thing that must NOT be swallowed: the answer itself."""
        config = make_config()
        increment_usage(dynamodb, config, invokes=100, input_tokens=0, output_tokens=0, app_id="x")
        with pytest.raises(QuotaExceededError):
            check_quota(dynamodb, config, app_id="x")


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.quota",
        preview=False,
    )
