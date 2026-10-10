# -*- coding: utf-8 -*-

"""
CDK entry point: `npx aws-cdk@2 deploy --all` from infra/ (see the infra-*
tasks in mise.toml). Deploys to the account and region of the current AWS
credentials; us-east-1 unless CDK_DEFAULT_REGION says otherwise.
"""

import os

import aws_cdk as cdk

from citypulse_infra.stacks import (
    AgentRuntimeStack,
    GithubDeployStack,
    McpRuntimeStack,
    ObservabilityStack,
    SecretsStack,
)

app = cdk.App()
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

secrets = SecretsStack(app, "CityPulseSecrets", env=env)
mcp = McpRuntimeStack(app, "CityPulseMcpRuntime", secrets=secrets, env=env)
AgentRuntimeStack(app, "CityPulseAgentRuntime", secrets=secrets, mcp=mcp, env=env)
ObservabilityStack(app, "CityPulseObservability", env=env)
GithubDeployStack(app, "CityPulseGithubDeploy", repo="chenziliang1/311-analytics-agent", env=env)

cdk.Tags.of(app).add("project", "citypulse-311-lens")
app.synth()
