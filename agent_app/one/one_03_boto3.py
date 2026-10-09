# -*- coding: utf-8 -*-

"""AWS Boto3 mixin for the One class."""

import typing as T
from functools import cached_property

import boto3

if T.TYPE_CHECKING:  # pragma: no cover
    from .one_00_main import One


class Boto3Mixin:
    """
    Mixin providing the AWS session and the service clients built from it.

    Every client is a method rather than a field so that a deployment which uses
    none of them never builds one -- constructing a boto3 client parses that
    service's model, which is not free on a serverless cold start.
    """

    @cached_property
    def boto_ses(self: "One") -> boto3.Session:  # pragma: no cover - needs AWS
        """Create a boto3 session with configured AWS credentials."""
        return boto3.Session(
            region_name=self.config.aws_region,
            aws_access_key_id=self.config.aws_access_key_id,
            aws_secret_access_key=self.config.aws_secret_access_key,
        )

    def bedrock_runtime_client(self: "One"):
        """
        The Bedrock Runtime client, for invoking models.
        """
        return self.boto_ses.client("bedrock-runtime")

    def dynamodb_client(self: "One"):
        """The DynamoDB client, for the monthly spend counters."""
        return self.boto_ses.client("dynamodb")

    def bedrock_agentcore_client(self: "One"):
        """
        The AgentCore data-plane client, for handing a turn to the deployed
        agent. Its read timeout covers a whole turn, which can take a minute.
        """
        from botocore.config import Config as BotoConfig

        return self.boto_ses.client(
            "bedrock-agentcore",
            config=BotoConfig(read_timeout=300, retries={"max_attempts": 2}),
        )

    def cloudwatch_client(self: "One"):
        """The CloudWatch client, for the operational metrics."""
        return self.boto_ses.client("cloudwatch")

    def metrics(self: "One", component: str):
        """
        Where this process publishes its operational metrics, tagged with
        ``component`` (``agent``, ``mcp``). A no-op without METRICS_NAMESPACE.
        """
        from ..metrics import Metrics, NoMetrics

        if not self.config.metrics_namespace:
            return NoMetrics()
        return Metrics(self.cloudwatch_client, self.config.metrics_namespace, component)
