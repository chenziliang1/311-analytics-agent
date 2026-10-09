# -*- coding: utf-8 -*-

"""
One AgentCore Runtime and the execution role it runs as.

The role follows AWS's documented execution role for AgentCore Runtime, cut to
this app: it may pull this image, write its own runtime's logs and traces,
publish metrics in two namespaces, and read the secrets it is handed --
nothing else. In particular it has no bedrock:InvokeModel, because the models
are reached over the Anthropic API with a key from Secrets Manager.
"""

from pathlib import Path

from aws_cdk import Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

#: The repo root: the image's build context. Filtered by /.dockerignore.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where the app publishes its own metrics (agent_app/metrics.py).
METRICS_NAMESPACE = "CityPulse"


def agent_image(scope: Construct, id: str) -> ecr_assets.DockerImageAsset:
    """The one image both runtimes run; CDK builds it once and pushes it to ECR."""
    return ecr_assets.DockerImageAsset(
        scope,
        id,
        directory=str(REPO_ROOT),
        file="infra/docker/Dockerfile",
        platform=ecr_assets.Platform.LINUX_ARM64,
    )


class AgentCoreRuntime(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        runtime_name: str,
        protocol: str,
        image: ecr_assets.DockerImageAsset,
        environment: dict[str, str],
        secrets: list[secretsmanager.ISecret],
        description: str,
    ):
        super().__init__(scope, id)
        stack = Stack.of(self)
        region, account = stack.region, stack.account
        log_groups = f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/"

        self.role = iam.Role(
            self,
            "ExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
                },
            ),
            description=f"Execution role for the {runtime_name} AgentCore runtime",
        )
        image.repository.grant_pull(self.role)
        self.role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken"], resources=["*"],
        ))
        self.role.add_to_policy(iam.PolicyStatement(
            actions=["logs:DescribeLogStreams", "logs:CreateLogGroup"],
            resources=[f"{log_groups}*"],
        ))
        self.role.add_to_policy(iam.PolicyStatement(
            actions=["logs:DescribeLogGroups"],
            resources=[f"arn:aws:logs:{region}:{account}:log-group:*"],
        ))
        self.role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"{log_groups}*:log-stream:*"],
        ))
        self.role.add_to_policy(iam.PolicyStatement(
            actions=[
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
                "xray:GetSamplingRules",
                "xray:GetSamplingTargets",
            ],
            resources=["*"],
        ))
        self.role.add_to_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": ["bedrock-agentcore", METRICS_NAMESPACE]}},
        ))
        self.role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock-agentcore:GetWorkloadAccessToken",
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
            ],
            resources=[
                f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default",
                f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default/workload-identity/{runtime_name}-*",
            ],
        ))
        for secret in secrets:
            secret.grant_read(self.role)

        self.runtime = agentcore.CfnRuntime(
            self,
            "Runtime",
            agent_runtime_name=runtime_name,
            description=description,
            role_arn=self.role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri,
                ),
            ),
            # PUBLIC, not VPC: no NAT gateway or interface endpoints, so no
            # fixed monthly cost. Private subnets are the production form; this is a demo that sits idle most of the time.
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC",
            ),
            protocol_configuration=protocol,
            environment_variables={
                **environment,
                "APP_SECRET_IDS": ",".join(s.secret_arn for s in secrets),
                "METRICS_NAMESPACE": METRICS_NAMESPACE,
            },
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                # A chat sits idle between questions; 15 minutes keeps a
                # follow-up warm without paying for an abandoned tab all day.
                idle_runtime_session_timeout=900,
                max_lifetime=8 * 3600,
            ),
        )
        # The role's policy must exist before AgentCore tries to pull the image.
        self.runtime.node.add_dependency(self.role)

    @property
    def arn(self) -> str:
        return self.runtime.attr_agent_runtime_arn
