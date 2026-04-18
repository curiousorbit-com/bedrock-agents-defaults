"""Tests for AgentStack.

Synthesizes Compute + Agent stacks together in a CDK app and asserts the
shape of the generated CloudFormation: one ``CfnAgent`` + one ``CfnAgentAlias``,
Claude Haiku 4.5 via the US inference profile, a non-empty OpenAPI schema
payload on the action group, and — the core regression assertion for this
repo — that the ``fixed`` instruction variant carries the MANDATORY DEFAULTS
lockdown while ``broken`` does not.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Template

from stacks.agent_stack import AgentStack
from stacks.compute_stack import ComputeStack

_TEST_ENV = cdk.Environment(account="111111111111", region="us-east-1")


def _template(variant: str = "fixed") -> Template:
    app = cdk.App(context={"instructionVariant": variant})
    compute = ComputeStack(app, "test-compute", env=_TEST_ENV)
    agent = AgentStack(app, "test-agents", compute_stack=compute, env=_TEST_ENV)
    return Template.from_stack(agent)


def _instruction(template: Template) -> str:
    """Return the agent's Instruction with whitespace collapsed.

    The instruction files are hand-wrapped for readability; tests look at
    semantic phrases (``"Do NOT ask the user to confirm"``,
    ``"account_id, date, ..."``) that may span wrap boundaries. Collapsing
    whitespace here means line wraps stay a presentation choice, not a
    test constraint.
    """
    agents = template.find_resources("AWS::Bedrock::Agent")
    assert len(agents) == 1, f"expected exactly one agent, found {len(agents)}"
    (props,) = (r["Properties"] for r in agents.values())
    instruction = props.get("Instruction")
    assert isinstance(instruction, str) and instruction, (
        "agent must have a non-empty Instruction"
    )
    return " ".join(instruction.split())


def test_exactly_one_agent_and_alias():
    template = _template()
    template.resource_count_is("AWS::Bedrock::Agent", 1)
    template.resource_count_is("AWS::Bedrock::AgentAlias", 1)


def test_foundation_model_is_us_inference_profile():
    """Must be us.* not global.* — Bedrock Agents rejects the global profile."""
    template = _template()
    agents = template.find_resources("AWS::Bedrock::Agent")
    for resource in agents.values():
        model = resource["Properties"].get("FoundationModel")
        assert model == "us.anthropic.claude-haiku-4-5-20251001-v1:0", (
            f"unexpected foundation model: {model}"
        )


def test_action_group_has_non_empty_openapi_payload():
    template = _template()
    agents = template.find_resources("AWS::Bedrock::Agent")
    (props,) = (r["Properties"] for r in agents.values())
    action_groups = props.get("ActionGroups") or []
    assert len(action_groups) == 1
    payload = action_groups[0]["ApiSchema"]["Payload"]
    assert isinstance(payload, str) and len(payload) > 0
    assert "openapi" in payload.lower()
    # Sanity — the two SaaS operations should be present in the schema.
    assert "get_account_summary" in payload
    assert "get_top_accounts" in payload


def test_outputs_expose_agent_identifiers():
    template = _template()
    outputs = template.find_outputs("*")
    for key in ("AgentId", "AgentAliasId", "AgentAliasArn", "InstructionVariant"):
        assert key in outputs, f"missing CfnOutput {key}"


def test_agent_role_has_inline_marketplace_statement():
    """Execution role must carry marketplace perms inline, not as a separate policy.

    Inline matters: Bedrock validates the role at CreateAgent time and a
    separate ``AWS::IAM::Policy`` resource attaches later.
    """
    template = _template()
    roles = template.find_resources(
        "AWS::IAM::Role",
        {"Properties": {"AssumeRolePolicyDocument": {"Statement": [
            {"Principal": {"Service": "bedrock.amazonaws.com"}}
        ]}}},
    )
    assert len(roles) == 1
    (role_props,) = (r["Properties"] for r in roles.values())
    inline = role_props.get("Policies") or []
    assert len(inline) >= 1, "agent role must have at least one inline policy"
    statements = inline[0]["PolicyDocument"]["Statement"]
    marketplace_actions = {
        "aws-marketplace:ViewSubscriptions",
        "aws-marketplace:Subscribe",
        "aws-marketplace:Unsubscribe",
    }
    found = False
    for stmt in statements:
        actions = stmt.get("Action")
        actions = actions if isinstance(actions, list) else [actions]
        if marketplace_actions.issubset(set(actions)):
            assert (
                stmt.get("Condition", {}).get("StringEquals", {}).get(
                    "aws:CalledViaLast"
                )
                == "bedrock.amazonaws.com"
            ), "marketplace statement must scope CalledViaLast to bedrock"
            found = True
            break
    assert found, "agent role must carry inline aws-marketplace:* statement"


# ---------------------------------------------------------------------------
# Instruction variant tests — the core of this repo.
# ---------------------------------------------------------------------------


def test_fixed_variant_has_mandatory_defaults_lockdown():
    """`fixed` instruction must frame defaults imperatively so Haiku applies silently.

    The bug: descriptive phrasing ("Default date range: X when user omits...")
    lets Haiku read the default as an open question and ask the user to
    confirm dates/metrics. The fix is commandy phrasing — `MANDATORY
    DEFAULTS ... Do NOT ask ... Apply them and answer` — that leaves no room
    to interpret 'omits dates' as 'needs clarification'.

    Mirrors the upstream regression test from the Insight AWS Summit fix
    (MR !44, ``test_specialists_have_mandatory_defaults_lockdown``).
    """
    instruction = _instruction(_template("fixed"))
    assert "MANDATORY DEFAULTS" in instruction, (
        "fixed instruction must use imperative 'MANDATORY DEFAULTS' framing"
    )
    assert "Do NOT ask the user to confirm" in instruction, (
        "fixed instruction must explicitly forbid asking the user to confirm defaults"
    )
    assert "never for a missing date or metric" in instruction, (
        "fixed instruction must explicitly rule out clarification when only "
        "dates/metrics are missing"
    )


def test_broken_variant_does_not_have_mandatory_defaults_lockdown():
    """`broken` instruction must NOT carry the fix — it's the reproducer.

    If someone accidentally ports the lockdown phrasing into broken.txt, the
    video demo loses its bug, so lock that down in tests.
    """
    instruction = _instruction(_template("broken"))
    assert "MANDATORY DEFAULTS" not in instruction, (
        "broken instruction must NOT carry 'MANDATORY DEFAULTS' — it's the bug repro"
    )
    assert "Do NOT ask" not in instruction, (
        "broken instruction must NOT forbid asking — that's the fix, not the bug"
    )


def test_both_variants_lock_to_real_schema_columns():
    """Schema lock should be present in both variants — it's orthogonal to the defaults bug."""
    expected = (
        "account_id, date, active_users, feature_adoption_pct, mrr_cad, "
        "health_score"
    )
    for variant in ("broken", "fixed"):
        instruction = _instruction(_template(variant))
        assert expected in instruction, (
            f"{variant} instruction must list the real SaaS columns: {expected}"
        )


def test_both_variants_carry_default_date_window():
    """Default quarter window should be present in both variants."""
    for variant in ("broken", "fixed"):
        instruction = _instruction(_template(variant))
        assert "2026-01-01 to 2026-03-31" in instruction, (
            f"{variant} instruction must carry the default quarter window"
        )


def test_default_variant_is_fixed():
    """No context override should land on the `fixed` instruction — safest default."""
    app = cdk.App()
    compute = ComputeStack(app, "test-compute", env=_TEST_ENV)
    agent = AgentStack(app, "test-agents", compute_stack=compute, env=_TEST_ENV)
    template = Template.from_stack(agent)
    instruction = _instruction(template)
    assert "MANDATORY DEFAULTS" in instruction
    assert agent.instruction_variant == "fixed"
