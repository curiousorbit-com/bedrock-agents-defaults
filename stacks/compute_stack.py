"""ComputeStack: one Lambda backing the Account Performance IQ action group.

Python 3.12, 512 MB, 60s timeout. Runs entirely on mock in-memory data — no
S3, Glue, or Athena in this repo. Production Bedrock Agent builds put Athena
query execution here (see CLAUDE.md reference); stripped out to keep the
tutorial surface minimal.

Grants ``lambda:InvokeFunction`` to ``bedrock.amazonaws.com`` so the
:class:`AgentStack` action group attaches cleanly.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as aws_lambda
from constructs import Construct

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LAMBDA_SRC = _REPO_ROOT / "lambdas" / "account_performance"


class ComputeStack(Stack):
    """Lambda function backing the Account Performance IQ action group."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(
            self,
            "AccountPerformanceRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        self.account_performance_fn = aws_lambda.Function(
            self,
            "AccountPerformanceFn",
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=aws_lambda.Code.from_asset(str(_LAMBDA_SRC)),
            memory_size=512,
            timeout=Duration.seconds(60),
            role=role,
        )
        self.account_performance_fn.apply_removal_policy(RemovalPolicy.DESTROY)
        self.account_performance_fn.grant_invoke(
            iam.ServicePrincipal("bedrock.amazonaws.com")
        )

        CfnOutput(
            self,
            "AccountPerformanceLambdaArn",
            value=self.account_performance_fn.function_arn,
            description="ARN of the Account Performance IQ action group Lambda.",
        )
