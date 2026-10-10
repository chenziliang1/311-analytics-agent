# -*- coding: utf-8 -*-

"""
The deployment, as four stacks, plus the role GitHub Actions deploys them with.

    Secrets -> McpRuntime -> AgentRuntime -> Observability
    GithubDeploy

Skipped on purpose: the network stack (both runtimes run in AgentCore's PUBLIC
network mode, so there is no VPC, NAT or endpoint to pay for while idle) and
the knowledge base stack (the knowledge base is deferred). The models are
reached over the Anthropic API, so no role has bedrock:InvokeModel.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, SecretValue, Stack
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from constructs import Construct

from .runtime import METRICS_NAMESPACE, AgentCoreRuntime, agent_image

#: Keys each secret holds. The values are filled after deploy with
#: `agent-app push-secrets`, never written here: CDK only creates the shape.
LLM_SECRET_KEYS = ["ANTHROPIC_API_KEY"]
DB_SECRET_KEYS = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASS", "DB_NAME"]

#: Claude Sonnet 5.5 list price per million tokens, for the cost estimate on
#: the dashboard. An estimate, not the bill: update it with the price.
INPUT_USD_PER_MTOK = 2.0
OUTPUT_USD_PER_MTOK = 10.0


def _placeholder_secret(scope: Construct, id: str, name: str, keys: list[str], description: str):
    return secretsmanager.Secret(
        scope,
        id,
        secret_name=name,
        description=description,
        secret_object_value={k: SecretValue.unsafe_plain_text("") for k in keys},
        removal_policy=RemovalPolicy.DESTROY,
    )


class SecretsStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.llm = _placeholder_secret(
            self, "Llm", "citypulse/llm", LLM_SECRET_KEYS, "Model API key for the agent and SQL writer"
        )
        self.db = _placeholder_secret(
            self, "Db", "citypulse/db", DB_SECRET_KEYS, "Warehouse connection for the MCP tools"
        )


class McpRuntimeStack(Stack):
    """The data tools: warehouse_query and visualize, over MCP."""

    def __init__(self, scope: Construct, id: str, *, secrets: SecretsStack, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.mcp = AgentCoreRuntime(
            self,
            "Mcp",
            runtime_name="citypulse_mcp",
            protocol="MCP",
            image=agent_image(self, "Image"),
            environment={"AGENT_APP_ROLE": "mcp"},
            # The writer needs the model key; only this runtime sees the database.
            secrets=[secrets.db, secrets.llm],
            description="CityPulse 311 Lens data tools (warehouse_query, visualize)",
        )
        CfnOutput(self, "McpRuntimeArn", value=self.mcp.arn)


class AgentRuntimeStack(Stack):
    """The agent, plus the one identity allowed to call it from the web app."""

    def __init__(
        self, scope: Construct, id: str, *, secrets: SecretsStack, mcp: McpRuntimeStack, **kwargs
    ):
        super().__init__(scope, id, **kwargs)
        mcp_arn = mcp.mcp.arn
        self.agent = AgentCoreRuntime(
            self,
            "Agent",
            runtime_name="citypulse_agent",
            protocol="HTTP",
            image=agent_image(self, "Image"),
            environment={"AGENT_APP_ROLE": "agent", "MCP_RUNTIME_ARN": mcp_arn},
            secrets=[secrets.llm],
            description="CityPulse 311 Lens agent",
        )
        # The agent reaches its tools only through AgentCore, signed with this role.
        self.agent.role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=[mcp_arn, f"{mcp_arn}/*"],
        ))

        # Vercel has no IAM role, so the web app signs with this user's keys.
        # It can do exactly one thing: invoke this agent. Create its access key
        # by hand; a key minted here would sit in the CloudFormation template.
        self.web_invoker = iam.User(self, "WebInvoker", user_name="citypulse-web-invoker")
        self.web_invoker.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=[self.agent.arn, f"{self.agent.arn}/*"],
        ))

        CfnOutput(self, "AgentRuntimeArn", value=self.agent.arn)
        CfnOutput(self, "WebInvokerUser", value=self.web_invoker.user_name)


def _metric(name: str, component: str, stat: str = "Sum", period: Duration = Duration.minutes(5), **dims):
    return cw.Metric(
        namespace=METRICS_NAMESPACE,
        metric_name=name,
        dimensions_map={"Component": component, **dims},
        statistic=stat,
        period=period,
    )


#: Every status warehouse_query reports (agent_app/mcp_server/models.py).
QUERY_STATUSES = ["ok", "empty", "needs_clarification", "blocked", "timeout", "sql_error"]


class ObservabilityStack(Stack):
    """The dashboard and alarms, on the app's own metrics."""

    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)
        self.topic = sns.Topic(self, "Alarms", topic_name="citypulse-alarms")

        by_status = {s: _metric("WarehouseQueries", "mcp", Status=s) for s in QUERY_STATUSES}
        total = cw.MathExpression(
            expression=" + ".join(f"FILL({s}, 0)" for s in QUERY_STATUSES),
            using_metrics={s: m for s, m in by_status.items()},
            label="All queries",
            period=Duration.minutes(5),
        )
        error_rate = cw.MathExpression(
            expression=(
                "100 * (FILL(sql_error, 0) + FILL(timeout, 0)) / "
                f"IF(({total.expression}) > 0, ({total.expression}), 1)"
            ),
            using_metrics=dict(by_status),
            label="Tool error rate (%)",
            period=Duration.minutes(5),
        )
        turn_p95 = _metric("TurnLatency", "agent", stat="p95")
        tokens = {
            "ai": _metric("InputTokens", "agent", period=Duration.days(1)),
            "ao": _metric("OutputTokens", "agent", period=Duration.days(1)),
            "wi": _metric("InputTokens", "mcp", period=Duration.days(1)),
            "wo": _metric("OutputTokens", "mcp", period=Duration.days(1)),
        }
        daily_cost = cw.MathExpression(
            expression=(
                f"(FILL(ai,0) + FILL(wi,0)) * {INPUT_USD_PER_MTOK} / 1000000 + "
                f"(FILL(ao,0) + FILL(wo,0)) * {OUTPUT_USD_PER_MTOK} / 1000000"
            ),
            using_metrics=tokens,
            label="Estimated model cost (USD/day)",
            period=Duration.days(1),
        )

        dashboard = cw.Dashboard(self, "Dashboard", dashboard_name="CityPulse-311-Lens")
        dashboard.add_widgets(
            cw.GraphWidget(
                title="1. Warehouse queries by status",
                left=list(by_status.values()),
                stacked=True,
                width=12,
            ),
            cw.GraphWidget(title="2. Tool error rate (%)", left=[error_rate], width=12),
        )
        dashboard.add_widgets(
            cw.GraphWidget(
                title="3. Latency p50 / p95 (s)",
                left=[
                    _metric("TurnLatency", "agent", stat="p50"),
                    turn_p95,
                    _metric("WarehouseQueryLatency", "mcp", stat="p95"),
                ],
                width=12,
            ),
            cw.GraphWidget(
                title="4. Tokens and estimated cost per day",
                left=list(tokens.values()),
                right=[daily_cost],
                width=12,
            ),
        )
        dashboard.add_widgets(
            cw.GraphWidget(
                title="5. Cold starts (first turn in a microVM)",
                left=[_metric("ColdStartTurns", "agent", period=Duration.hours(1))],
                width=12,
            ),
            cw.GraphWidget(
                title="6. Questions per day",
                left=[_metric("Turns", "agent", period=Duration.days(1))],
                width=12,
            ),
        )

        alarms = [
            cw.Alarm(
                self, "ToolErrorRate",
                alarm_description="More than 10% of warehouse queries failed or timed out over 5 minutes",
                metric=error_rate, threshold=10, evaluation_periods=1,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            ),
            cw.Alarm(
                self, "TurnLatencyP95",
                # The doc's 12s cannot hold with two model calls per question
                # (agent, then SQL writer): the golden set measures a 13.4s p95
                # warm (the eval gate is 15s), and a cold start takes 30-40s.
                # 30s for 10 minutes flags a real regression, not one cold start.
                alarm_description="p95 time to answer above 30s for 10 minutes",
                metric=turn_p95, threshold=30, evaluation_periods=2,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            ),
            cw.Alarm(
                self, "BlockedQueries",
                alarm_description="More than 20 refused queries in an hour: probing for writes or personal data",
                metric=_metric("WarehouseQueries", "mcp", period=Duration.hours(1), Status="blocked"),
                threshold=20, evaluation_periods=1,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            ),
            cw.Alarm(
                self, "DailyModelCost",
                # The doc's monthly cap was $600; a day at $20 is on that pace.
                alarm_description="Estimated model spend above $20 in a day",
                metric=daily_cost, threshold=20, evaluation_periods=1,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            ),
        ]
        for alarm in alarms:
            alarm.add_alarm_action(cw_actions.SnsAction(self.topic))

        CfnOutput(self, "AlarmTopicArn", value=self.topic.topic_arn)


#: CDK's default bootstrap qualifier: the roles `cdk bootstrap` created.
CDK_QUALIFIER = "hnb659fds"


class GithubDeployStack(Stack):
    """
    The role GitHub Actions deploys with, through OIDC: no AWS key is stored
    in GitHub. Only workflow runs on the repository's main branch can assume
    it, and all it may do is assume CDK's own bootstrap roles, which hold the
    actual deploy permissions -- the same path a local `cdk deploy` takes.

    The account already has the GitHub OIDC provider (one per account), so it
    is referenced, not created.

    :param subject_prefix: The token's ``sub`` up to the ref, as the repository
        sends it (``gh api repos/<repo>/actions/oidc/customization/sub``). This
        repository uses GitHub's immutable form, ``repo:<owner>@<owner id>/
        <name>@<repo id>``: a deleted and re-created repository of the same name
        gets a new id, so it cannot take over the role.
    """

    def __init__(self, scope: Construct, id: str, *, subject_prefix: str, branch: str = "main", **kwargs):
        super().__init__(scope, id, **kwargs)
        provider_arn = f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com"
        role = iam.Role(
            self,
            "DeployRole",
            role_name="citypulse-github-deploy",
            description=f"GitHub Actions deploys from {subject_prefix}, branch {branch}",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.FederatedPrincipal(
                provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": f"{subject_prefix}:ref:refs/heads/{branch}",
                    }
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole", "sts:TagSession"],
                resources=[f"arn:aws:iam::{self.account}:role/cdk-{CDK_QUALIFIER}-*-{self.account}-{self.region}"],
            )
        )
        CfnOutput(self, "DeployRoleArn", value=role.role_arn)
