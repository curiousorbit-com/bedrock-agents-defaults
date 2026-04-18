# SPECS — bedrock-agents-defaults

Technical specification for the repo. See `README.md` for tutorial narrative
and quick-start; this file is the reference for what the code actually does
and why.

## Scope

One Bedrock Agent specialist + one Lambda action group. Purpose:

1. Demonstrate a Claude Haiku 4.5 clarification-loop pitfall that triggers
   when default values are framed *descriptively* in an agent instruction
   (`instructions/broken.txt`).
2. Demonstrate the fix: *imperative* phrasing
   (`instructions/fixed.txt`).
3. Ship a portable regression test
   (`tests/test_agent_stack.py::test_fixed_variant_has_mandatory_defaults_lockdown`)
   that can be dropped into any Bedrock Agents repo.

Explicitly **out of scope**: supervisor/router agents, real data
infrastructure (S3/Glue/Athena), QuickSight or chat UI, CI/CD. A production
multi-specialist pattern is sketched at the end of `README.md`.

## Domain

Generic SaaS customer success / account analytics (no industry-specific
framing).

| Field | Type |
|---|---|
| `account_id` | string |
| `date` | date |
| `active_users` | int |
| `feature_adoption_pct` | float |
| `mrr_cad` | float |
| `health_score` | float (0–100) |

Defaults baked into the instructions:

| Default | Value |
|---|---|
| Date window | `2026-01-01` to `2026-03-31` |
| Ranking metric | `mrr_cad` (with inverse for "worst"/"underperforming") |
| Top-N | `5` |

Canonical reproducer query:

> *"Who's my worst-performing account this quarter?"*

## Architecture

```
AccountPerformanceIQ (specialist agent, no supervisor)
    └── Lambda action group (mock in-memory SaaS data)
```

Two CDK stacks wired directly:

- `stacks/compute_stack.py::ComputeStack` — `AccountPerformanceFn` Lambda
  (Python 3.12, 512 MB, 60s timeout). Mock `_ACCOUNTS` list in
  `lambdas/account_performance/handler.py`. No external data dependencies.
- `stacks/agent_stack.py::AgentStack` — `AccountPerformanceIQ` Bedrock agent
  with OpenAPI-schema action group pointing at the Lambda, plus a
  `live` agent alias.

## Instruction variants

Selected at synth / deploy time via CDK context:

```
cdk synth -c instructionVariant=fixed     # default
cdk synth -c instructionVariant=broken
```

Both files live at `instructions/*.txt` and are read at synth time. Only
the agent's `Instruction` property differs between variants; the Lambda,
IAM role, action group, and schema are identical.

### `fixed.txt` — required phrasing

The imperative lockdown hinges on three phrases the regression test
asserts:

1. `MANDATORY DEFAULTS` — header framing defaults as commands.
2. `Do NOT ask the user to confirm` — explicit negation of the default
   Claude behaviour to clarify.
3. `never for a missing date or metric` — rules out the specific
   clarification the pitfall produces.

Any phrasing that preserves all three survives the test. Paraphrasing
without all three should be considered a regression.

### `broken.txt` — pitfall reproducer

Descriptive phrasing ("Default date range: X when the user omits a date
range") that triggers the pre-clarification behaviour. Must **not** carry
`MANDATORY DEFAULTS` or `Do NOT ask` — a test asserts this so the demo
doesn't silently lose its pitfall.

## Foundation model

`us.anthropic.claude-haiku-4-5-20251001-v1:0` (US cross-region inference
profile).

The `global.` profile works with `bedrock-runtime:InvokeModel` directly but
is rejected by Bedrock Agents at `CreateAgent` time with
`AccessDenied for operation 'AWS::Bedrock::Agent'` — no IAM fix resolves
this. `us.` is the only viable option for Agents today.

### Why the `us.*` profile in us-east-1

Strictly speaking, in `us-east-1` you can reference Haiku 4.5 directly by
its foundation-model ARN (`anthropic.claude-haiku-4-5-20251001-v1:0`) and
skip the inference profile entirely — Haiku is native to `us-east-1`. The
profile only becomes mandatory when deploying outside the model's native
region (e.g., `ca-central-1`, `eu-*`, `ap-*`), where cross-region
inference is the only way to reach Anthropic models from Bedrock Agents.

This repo uses the `us.*` profile even in `us-east-1` on purpose:

- **Portability** — a viewer who copies the pattern into `ca-central-1` or
  any other non-US region needs the profile, and the `global.` → `us.*`
  lesson is part of the point.
- **One code path** — the IAM shape stays identical regardless of deploy
  region.
- **No downside in `us-east-1`** — the profile adds no latency or cost;
  Bedrock routes to a US region either way.

## IAM — the four non-obvious requirements

All four are baked into `stacks/agent_stack.py`. Skipping any one produces
a distinct `AccessDenied` at deploy or runtime.

### 1. `us.*` inference profile (see above)

### 2. Inline policies, not `add_to_policy()`

```python
iam.Role(
    self, "AgentRole",
    assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
    inline_policies={"AgentExecution": iam.PolicyDocument(statements=[...])},
)
```

CDK's `role.add_to_policy(...)` creates a separate `AWS::IAM::Policy`
resource. CloudFormation deploys that resource *after* the agent, so
Bedrock's `CreateAgent` validation runs before permissions attach and
surfaces as `AccessDenied`. Passing `inline_policies={...}` on the `Role`
constructor gives atomic provisioning.

### 3. Marketplace subscription permissions

```python
iam.PolicyStatement(
    actions=[
        "aws-marketplace:ViewSubscriptions",
        "aws-marketplace:Subscribe",
        "aws-marketplace:Unsubscribe",
    ],
    resources=["*"],
    conditions={"StringEquals": {"aws:CalledViaLast": "bedrock.amazonaws.com"}},
)
```

Bedrock completes a Marketplace subscription on the caller's behalf for
Anthropic models. Without these actions the runtime invoke returns a 403
that explicitly names them. The `CalledViaLast` condition scopes the
permission to calls arriving through Bedrock so the role can't be used
outside that path.

### 4. Explicit model ARNs, not wildcards

```python
resources=[
    f"arn:aws:bedrock:{region}:{account}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
    f"arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
]
```

Wildcards like `arn:aws:bedrock:*::foundation-model/anthropic.*` are
accepted by IAM but behave unreliably under Bedrock Agents' permission
evaluation. The explicit pair — inference profile in the deploy account
plus the underlying foundation-model ARN — is the pattern that deploys
cleanly.

## Lambda action group contract

OpenAPI-schema action group (not function-schema). Bedrock sends:

```json
{
  "apiPath": "/get_account_summary",
  "httpMethod": "POST",
  "actionGroup": "AccountPerformanceActions",
  "parameters": [{"name": "account_id", "value": "ACC-101"}, ...]
}
```

The handler dispatches on `event["apiPath"].lstrip("/")` and must return:

```json
{
  "messageVersion": "1.0",
  "response": {
    "actionGroup": "...",
    "apiPath": "...",
    "httpMethod": "POST",
    "httpStatusCode": 200,
    "responseBody": {"application/json": {"body": "<json string>"}}
  }
}
```

Getting the envelope wrong surfaces at runtime as
`"APIPath in Lambda response doesn't match input"`.

## Deploy model

Local `cdk deploy` only. No CI/CD.

- Deploying requires AWS admin (or equivalently-scoped) credentials on the
  operator's machine.
- Deploying requires Claude Haiku 4.5 model access to be enabled on the
  target AWS account (Bedrock console, `Manage model access`).
- Deploy → test → tear down with `cdk destroy`. Nothing is stateful.

Public-repo viewers clone, set their own `CDK_DEFAULT_ACCOUNT`, and run
`cdk deploy` locally. This mirrors how the repo is used in the tutorial.

## Tests

`tests/test_agent_stack.py` synthesizes the CDK app and asserts the
generated CloudFormation. Key assertions:

| Test | Guards |
|---|---|
| `test_fixed_variant_has_mandatory_defaults_lockdown` | the three required phrases in `fixed.txt` |
| `test_broken_variant_does_not_have_mandatory_defaults_lockdown` | `broken.txt` stays broken |
| `test_foundation_model_is_us_inference_profile` | `us.*` not `global.*` |
| `test_agent_role_has_inline_marketplace_statement` | marketplace perms present, inline, `CalledViaLast`-scoped |
| `test_both_variants_lock_to_real_schema_columns` | no hallucinated fields |
| `test_default_variant_is_fixed` | safe default on unconfigured synth |

Instruction text is compared with whitespace normalized, so hand-wrapping
the instruction files is a presentation choice and won't break tests.
