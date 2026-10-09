# -*- coding: utf-8 -*-

"""
AWS pieces the deployed runtimes need: SigV4 for calling another AgentCore
runtime, the MCP endpoint URL for one, and secrets from Secrets Manager.
"""

import json
import os
import urllib.parse

import httpx

#: The fixed host every AgentCore runtime is invoked through.
AGENTCORE_HOST = "bedrock-agentcore.{region}.amazonaws.com"

#: Headers worth signing. The rest (content-length, user-agent) can be changed
#: by the HTTP client after signing, which would break the signature.
_SIGNED_HEADERS = {"host", "content-type", "accept", "mcp-session-id", "mcp-protocol-version"}


def agentcore_invocation_url(runtime_arn: str, region: str, qualifier: str = "DEFAULT") -> str:
    """The URL a runtime is invoked at: its ARN, fully escaped, in the path."""
    escaped = urllib.parse.quote(runtime_arn, safe="")
    host = AGENTCORE_HOST.format(region=region)
    return f"https://{host}/runtimes/{escaped}/invocations?qualifier={qualifier}"


class SigV4Auth(httpx.Auth):
    """
    Sign each request with the caller's AWS credentials, as AgentCore's
    IAM-authenticated runtimes require. Credentials are read per request, so a
    role's rotating session credentials keep working.
    """

    requires_request_body = True

    def __init__(self, boto_session, region: str, service: str = "bedrock-agentcore"):
        self.boto_session = boto_session
        self.region = region
        self.service = service

    def auth_flow(self, request: httpx.Request):
        from botocore.auth import SigV4Auth as BotoSigV4
        from botocore.awsrequest import AWSRequest

        credentials = self.boto_session.get_credentials().get_frozen_credentials()
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers={k: v for k, v in request.headers.items() if k.lower() in _SIGNED_HEADERS},
        )
        BotoSigV4(credentials, self.service, self.region).add_auth(signed)
        for key, value in signed.headers.items():
            request.headers[key] = value
        yield request


def load_secrets_into_env(secret_ids: str, client=None) -> list[str]:
    """
    Copy every key of each JSON secret into the environment, without
    overwriting anything already set.

    How a container gets credentials without them sitting in its image or in
    its runtime configuration (which the console shows in plain text): the
    runtime gets only the secret names, and its IAM role may read those.

    :param secret_ids: Comma-separated Secrets Manager ids or ARNs.
    :return: The variable names that were set, for a log line without values.
    """
    if client is None:  # pragma: no cover - needs AWS
        import boto3

        client = boto3.client("secretsmanager")
    names = []
    for secret_id in (s.strip() for s in secret_ids.split(",") if s.strip()):
        values = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])
        for key, value in values.items():
            if key not in os.environ and value not in (None, ""):
                os.environ[key] = str(value)
                names.append(key)
    return names
