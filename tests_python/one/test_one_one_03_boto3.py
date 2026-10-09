# -*- coding: utf-8 -*-

"""
Tests for the AWS client wiring.

No AWS: a stub session records what was asked of it. What is worth checking is
that each client asks for the right service name -- a typo there fails at the
first call, in production, with a message about an unknown service rather than
about this line.
"""

from agent_app.one.one_03_boto3 import Boto3Mixin


class StubSession:
    def __init__(self):
        self.requested = []

    def client(self, service_name):
        self.requested.append(service_name)
        return f"<{service_name} client>"


class Wired(Boto3Mixin):
    """The mixin with its session replaced, so nothing reaches AWS."""

    def __init__(self, session):
        # boto_ses is a cached_property; seeding __dict__ is how you pre-empt it.
        self.__dict__["boto_ses"] = session


class TestClients:
    def test_dynamodb_client(self):
        session = StubSession()
        assert Wired(session).dynamodb_client() == "<dynamodb client>"
        assert session.requested == ["dynamodb"]

    def test_bedrock_runtime_client(self):
        session = StubSession()
        assert Wired(session).bedrock_runtime_client() == "<bedrock-runtime client>"
        assert session.requested == ["bedrock-runtime"]

    def test_clients_are_methods_not_cached_properties(self):
        """
        Building a boto3 client parses that service's model, so a deployment
        that uses neither must not pay for one at import time.
        """
        for name in ("dynamodb_client", "bedrock_runtime_client"):
            assert callable(getattr(Boto3Mixin, name))


if __name__ == "__main__":
    from agent_app.tests import run_cov_test

    run_cov_test(
        __file__,
        "agent_app.one.one_03_boto3",
        preview=False,
    )
