"""AgentStack: one Bedrock specialist agent (Account Performance IQ).

Single specialist — no supervisor. The pitfall this repo demonstrates
manifests on the specialist's instruction text (pre-clarifying on no-date
prompts), so a supervisor adds no explanatory value and doubles the deploy
surface. A production multi-specialist pattern with a ``SUPERVISOR_ROUTER``
parent is documented in ``README.md``.

Instruction variant is chosen via CDK context:

    cdk synth -c instructionVariant=broken     # reproduces the pitfall
    cdk synth -c instructionVariant=fixed      # MANDATORY DEFAULTS pattern (default)

The four non-obvious IAM requirements for Bedrock Agents
(us-profile + inline policies + marketplace subscription + explicit model
ARNs) all land in ``_agent_role``. See ``CLAUDE.md`` for the full write-up.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from constructs import Construct

from stacks.compute_stack import ComputeStack

# US cross-region inference profile. ``global.`` is accepted by bedrock-runtime
# Invoke but rejected by Bedrock Agents at CreateAgent time. Data plane traffic
# (Lambda, etc.) stays in the deploy region; only model inference crosses.
FOUNDATION_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
_MODEL_FM_SUFFIX = "anthropic.claude-haiku-4-5-20251001-v1:0"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTRUCTIONS_ROOT = _REPO_ROOT / "instructions"
_SCHEMAS_ROOT = _REPO_ROOT / "schemas"

_VALID_VARIANTS = ("broken", "fixed")


def _load_instruction(variant: str) -> str:
    if variant not in _VALID_VARIANTS:
        raise ValueError(
            f"instructionVariant must be one of {_VALID_VARIANTS!r}, got {variant!r}"
        )
    return (_INSTRUCTIONS_ROOT / f"{variant}.txt").read_text(encoding="utf-8")


def _load_schema() -> str:
    return (_SCHEMAS_ROOT / "account_performance.yaml").read_text(encoding="utf-8")


class AgentStack(Stack):
    """Single Bedrock specialist agent with a Lambda-backed action group."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        compute_stack: ComputeStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.compute_stack = compute_stack

        variant = self.node.try_get_context("instructionVariant") or "fixed"
        instruction = _load_instruction(variant)
        self.instruction_variant = variant

        region = Stack.of(self).region
        account = Stack.of(self).account

        fn = compute_stack.account_performance_fn

        # Explicit InvokeModel on both the inference profile ARN and the
        # underlying FM ARN. Wildcards behave unreliably under Bedrock Agents
        # IAM evaluation (OpenBrain memory 0ded0e36-*).
        invoke_model = iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
            ],
            resources=[
                f"arn:aws:bedrock:{region}:{account}:inference-profile/{FOUNDATION_MODEL_ID}",
                f"arn:aws:bedrock:*::foundation-model/{_MODEL_FM_SUFFIX}",
            ],
        )
        # Bedrock completes a Marketplace subscription on the caller's behalf
        # for Anthropic models. CalledViaLast scopes the perm to Bedrock-
        # originated calls only.
        marketplace = iam.PolicyStatement(
            actions=[
                "aws-marketplace:ViewSubscriptions",
                "aws-marketplace:Subscribe",
                "aws-marketplace:Unsubscribe",
            ],
            resources=["*"],
            conditions={
                "StringEquals": {"aws:CalledViaLast": "bedrock.amazonaws.com"},
            },
        )
        invoke_lambda = iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[fn.function_arn],
        )

        # Inline policies — NOT role.add_to_policy(). Bedrock's CreateAgent
        # validates the execution role at deploy time; a separate
        # AWS::IAM::Policy resource CFN creates after the agent will miss
        # that window and surface as AccessDenied.
        agent_role = iam.Role(
            self,
            "AccountPerformanceIQRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Execution role for the Account Performance IQ Bedrock agent.",
            inline_policies={
                "AgentExecution": iam.PolicyDocument(
                    statements=[invoke_model, marketplace, invoke_lambda]
                ),
            },
        )

        action_group = bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="AccountPerformanceActions",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                lambda_=fn.function_arn,
            ),
            api_schema=bedrock.CfnAgent.APISchemaProperty(payload=_load_schema()),
            action_group_state="ENABLED",
            description="Account Performance IQ action group (mock SaaS data).",
        )

        agent = bedrock.CfnAgent(
            self,
            "AccountPerformanceIQ",
            agent_name="AccountPerformanceIQ",
            agent_resource_role_arn=agent_role.role_arn,
            foundation_model=FOUNDATION_MODEL_ID,
            instruction=instruction,
            action_groups=[action_group],
            auto_prepare=True,
            description=(
                f"SaaS account performance specialist agent. Instruction "
                f"variant: {variant}."
            ),
            idle_session_ttl_in_seconds=1800,
        )
        agent.apply_removal_policy(RemovalPolicy.DESTROY)

        alias = bedrock.CfnAgentAlias(
            self,
            "AccountPerformanceIQAlias",
            agent_id=agent.attr_agent_id,
            agent_alias_name="live",
            description="Stable alias for Account Performance IQ.",
        )
        alias.apply_removal_policy(RemovalPolicy.DESTROY)

        self.agent = agent
        self.agent_alias = alias
        self.agent_role = agent_role

        CfnOutput(
            self,
            "AgentId",
            value=agent.attr_agent_id,
            description="Bedrock agent ID for Account Performance IQ.",
        )
        CfnOutput(
            self,
            "AgentAliasId",
            value=alias.attr_agent_alias_id,
            description="Bedrock agent alias ID (use this to invoke).",
        )
        CfnOutput(
            self,
            "AgentAliasArn",
            value=alias.attr_agent_alias_arn,
            description="Full ARN of the Account Performance IQ agent alias.",
        )
        CfnOutput(
            self,
            "InstructionVariant",
            value=variant,
            description="Which instructions/*.txt was baked into this agent.",
        )
