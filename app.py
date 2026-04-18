#!/usr/bin/env python3
"""CDK app entrypoint for bedrock-agents-defaults.

Two stacks, wired directly:
    compute (Lambda action group) → agents (Bedrock agent + action group).

Account/region come from CDK's standard env vars — run with
``CDK_DEFAULT_ACCOUNT=... CDK_DEFAULT_REGION=us-east-1 cdk synth``, or pass
``--context account=...`` / ``--context region=...``.

Context:
    instructionVariant  'broken' (reproduces the bug) or 'fixed' (MANDATORY
                        DEFAULTS pattern). Default is 'fixed'. Override with
                        ``cdk synth -c instructionVariant=broken``.
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.agent_stack import AgentStack
from stacks.compute_stack import ComputeStack

app = cdk.App()

account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = (
    app.node.try_get_context("region")
    or os.environ.get("CDK_DEFAULT_REGION")
    or "us-east-1"
)

env = cdk.Environment(account=account, region=region)
prefix = "bedrock-agents-defaults"

compute = ComputeStack(app, f"{prefix}-compute", env=env)
AgentStack(app, f"{prefix}-agents", compute_stack=compute, env=env)

app.synth()
