# -*- coding: utf-8 -*-

"""
The CDK templates, synthesized in memory. No AWS call and no Docker build:
synth only stages the image's build context.

What these pin down is what a careless edit would quietly break: the runtime
contracts, credentials arriving by secret name only, and every IAM grant being
scoped to one resource.
"""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("aws_cdk")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))

import aws_cdk as cdk  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402

from citypulse_infra.stacks import (  # noqa: E402
    AgentRuntimeStack,
    McpRuntimeStack,
    ObservabilityStack,
    SecretsStack,
)


@pytest.fixture(scope="module")
def templates():
    app = cdk.App()
    env = cdk.Environment(account="111122223333", region="us-east-1")
    secrets = SecretsStack(app, "S", env=env)
    mcp = McpRuntimeStack(app, "M", secrets=secrets, env=env)
    agent = AgentRuntimeStack(app, "A", secrets=secrets, mcp=mcp, env=env)
    obs = ObservabilityStack(app, "O", env=env)
    return {k: Template.from_stack(s) for k, s in dict(S=secrets, M=mcp, A=agent, O=obs).items()}


def runtime(template):
    [props] = [r["Properties"] for r in template.find_resources("AWS::BedrockAgentCore::Runtime").values()]
    return props


def test_runtimes_use_the_documented_protocols_and_public_network(templates):
    mcp, agent = runtime(templates["M"]), runtime(templates["A"])
    assert (mcp["ProtocolConfiguration"], agent["ProtocolConfiguration"]) == ("MCP", "HTTP")
    assert mcp["NetworkConfiguration"] == {"NetworkMode": "PUBLIC"}
    assert mcp["EnvironmentVariables"]["AGENT_APP_ROLE"] == "mcp"
    assert agent["EnvironmentVariables"]["AGENT_APP_ROLE"] == "agent"
    assert "MCP_RUNTIME_ARN" in agent["EnvironmentVariables"]


def test_no_credential_value_reaches_a_runtime(templates):
    """Only secret ARNs travel; the values are read at start by the role."""
    for key in ("M", "A"):
        env = runtime(templates[key])["EnvironmentVariables"]
        assert "APP_SECRET_IDS" in env
        assert not {"ANTHROPIC_API_KEY", "DB_PASS", "DB_HOST"} & set(env)


def test_only_the_tools_runtime_can_read_the_database_secret(templates):
    def secret_reads(template):
        policies = template.find_resources("AWS::IAM::Policy")
        return json.dumps(policies)

    assert "Db" in secret_reads(templates["M"])
    assert "Db" not in secret_reads(templates["A"])


def test_execution_role_trusts_only_agentcore_in_this_account(templates):
    templates["M"].has_resource_properties("AWS::IAM::Role", {
        "AssumeRolePolicyDocument": {"Statement": [Match.object_like({
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Condition": Match.object_like({"StringEquals": {"aws:SourceAccount": "111122223333"}}),
        })]},
    })


def test_no_role_can_invoke_bedrock_models(templates):
    for key in ("M", "A"):
        assert "bedrock:InvokeModel" not in json.dumps(templates[key].to_json())


def test_web_invoker_can_only_invoke_the_agent(templates):
    [user_policy] = [
        p["Properties"]["PolicyDocument"]["Statement"]
        for p in templates["A"].find_resources("AWS::IAM::Policy").values()
        if "WebInvoker" in json.dumps(p["Properties"].get("Users", []))
    ]
    assert [s["Action"] for s in user_policy] == ["bedrock-agentcore:InvokeAgentRuntime"]
    templates["A"].resource_count_is("AWS::IAM::AccessKey", 0)


def test_dockerignore_allows_what_the_build_needs_and_never_env():
    """
    CDK copies the context through .dockerignore before building, so a file
    missing from the allow-list is missing from the build -- the Dockerfile
    included, which a plain local `docker build` would never reveal.
    """
    root = Path(__file__).resolve().parents[1]
    lines = [l.strip() for l in (root / ".dockerignore").read_text().splitlines()]
    allowed = {l[1:] for l in lines if l.startswith("!")}
    assert lines[lines.index("*")] == "*"
    for needed in ("infra/docker/Dockerfile", "infra/docker/entrypoint.sh", "pyproject.toml", "uv.lock"):
        assert needed in allowed
    assert not any(".env" == a or a.startswith(".env ") for a in allowed)


def test_vercelignore_is_an_allow_list_without_secrets_or_the_seed():
    """The Vercel CLI uploads whatever this file does not skip, .env included."""
    root = Path(__file__).resolve().parents[1]
    lines = [l.strip() for l in (root / ".vercelignore").read_text().splitlines()]
    entries = [l for l in lines if l and not l.startswith("#")]
    assert entries[0] == "/*"
    allowed = {l[1:] for l in entries if l.startswith("!")}
    for needed in ("api", "app", "agent_app", "data", "package.json", "uv.lock", "vercel.json"):
        assert needed in allowed
    assert not allowed & {".env", "dataset", "infra", "tmp"}


def test_dashboard_and_four_alarms(templates):
    templates["O"].resource_count_is("AWS::CloudWatch::Dashboard", 1)
    templates["O"].resource_count_is("AWS::CloudWatch::Alarm", 4)
    body = json.dumps(templates["O"].to_json())
    for widget in ("1. Warehouse queries", "2. Tool error rate", "3. Latency", "4. Tokens", "5. Cold starts", "6. Questions per day"):
        assert widget in body
