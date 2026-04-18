# Stop Your Bedrock Agent From Asking Dumb Questions

Minimal, deployable repo for a subtle **instruction-tuning pitfall** on
Claude Haiku 4.5 in Amazon Bedrock Agents — and the prompt-craft fix.

When you phrase default values *descriptively*:

> Default date range: 2026-01-01 to 2026-03-31 when the user omits a date range.

Haiku reads "omits a date range" as **an invitation to ask for clarification**.
You get conversations like this:

> **You:** Who's my worst-performing account this quarter?
> **Agent:** Sure — what date range would you like to look at?

Phrase the same defaults *imperatively* and the problem goes away:

> **MANDATORY DEFAULTS** — apply these silently without asking.
> **Do NOT ask the user to confirm any of the defaults below.**
> Apply them and answer.
> - If the user does not specify a date range, use 2026-01-01 to 2026-03-31.

That's it. That's the repo.

Companion video: **Stop Your Bedrock Agent From Asking Dumb Questions**
(YouTube — link in the first comment once the video ships).

---

## What's in the repo

```
.
├── instructions/
│   ├── broken.txt      # descriptive defaults — triggers the pitfall
│   └── fixed.txt       # MANDATORY DEFAULTS — fixes it
├── schemas/
│   └── account_performance.yaml    # OpenAPI schema for the action group
├── lambdas/
│   └── account_performance/
│       └── handler.py              # mock SaaS data, no Athena
├── stacks/
│   ├── compute_stack.py            # the Lambda
│   └── agent_stack.py              # the Bedrock agent + action group + IAM
├── tests/
│   └── test_agent_stack.py         # CDK synth assertions incl. the regression test
├── app.py
├── cdk.json
└── pyproject.toml
```

One specialist agent (`AccountPerformanceIQ`), one Lambda, mock in-memory data.
Production deployments use Athena-backed Lambdas and a `SUPERVISOR_ROUTER`
parent agent on top of N specialists — see
[*Full production pattern*](#full-production-pattern) at the bottom. Everything
not needed to demonstrate the pitfall has been stripped out.

Full technical spec (domain, defaults, IAM rationale, test guards) is in
[`SPECS.md`](SPECS.md).

## The demo domain

Generic SaaS customer success / account analytics:

| Field | Type |
|---|---|
| `account_id` | string |
| `date` | date |
| `active_users` | int |
| `feature_adoption_pct` | float |
| `mrr_cad` | float |
| `health_score` | float (0–100) |

Defaults baked into the instructions:

- Default date window: `2026-01-01` to `2026-03-31`
- Default ranking metric: `mrr_cad`
- Default top-N: 5

Reproducer query (run it against the `broken` agent, then against `fixed`):

> *"Who's my worst-performing account this quarter?"*

`broken` will ask you which quarter and which metric. `fixed` will apply the
defaults silently and give you a ranked list.

## Quick start

You'll need Python 3.14, [uv](https://docs.astral.sh/uv/), the AWS CDK CLI, and
an AWS account with Claude Haiku 4.5 access enabled.

### Install + test

```bash
uv sync --dev
uv run pytest
```

### Synth

Default synth uses the **fixed** instructions:

```bash
export CDK_DEFAULT_ACCOUNT=<your-account-id>
export CDK_DEFAULT_REGION=us-east-1
uv run cdk synth
```

Switch to the **broken** variant to reproduce the pitfall on camera:

```bash
uv run cdk synth -c instructionVariant=broken
```

### Deploy

```bash
uv run cdk deploy --all
```

The stacks are named `bedrock-agents-defaults-compute` and
`bedrock-agents-defaults-agents`. Deploying will fail if Claude Haiku 4.5
model access is not approved on the account — that's an account-level AWS
Bedrock console step; CDK can't do it for you.

### Invoke the agent

After deploy, grab `AgentId` and `AgentAliasId` from the stack outputs:

```bash
aws bedrock-agent-runtime invoke-agent \
  --agent-id <AgentId> \
  --agent-alias-id <AgentAliasId> \
  --session-id $(uuidgen) \
  --input-text "Who's my worst-performing account this quarter?" \
  /tmp/response.json

jq -r '.completion.chunk.bytes | @base64d' /tmp/response.json
```

Or use the Bedrock Agents **Test** pane in the console.

## Switching between broken and fixed

```bash
# Deploy broken (for the on-camera repro)
uv run cdk deploy -c instructionVariant=broken

# Deploy fixed (the production-ready version)
uv run cdk deploy -c instructionVariant=fixed
```

Both reuse the same Lambda / action group / role — only the agent's
`Instruction` string changes. Redeploy takes ~1 minute.

## The regression test

`tests/test_agent_stack.py::test_fixed_variant_has_mandatory_defaults_lockdown`
asserts three things about the `fixed` instruction:

1. It contains the phrase `MANDATORY DEFAULTS`.
2. It explicitly forbids `Do NOT ask the user to confirm`.
3. It rules out clarification with `never for a missing date or metric`.

Remove any one and the test fails. This test is portable — drop it into your
own Bedrock Agents repo, point it at your instruction string, and you've got
a tripwire against the pitfall ever regressing.

## Why this happens (short version)

Claude models are heavily trained to ask clarifying questions when user
intent is ambiguous. Descriptive phrasing like

> Default date range: X **when the user omits a date range**.

looks to the model like **a description of an edge case the human should
resolve** — so the model asks. Imperative phrasing

> **MANDATORY** — apply the default silently. **Do NOT ask.**

looks to the model like **a direct instruction to not ask**. It complies.

The fix is not "add more guardrails." It is **phrasing defaults as commands,
not descriptions**.

## Full production pattern

This demo collapses a production pattern to keep the repro minimal. In
production, you'd typically wire this up as a `SUPERVISOR_ROUTER` parent
agent on top of several specialists, each backed by Athena over S3:

```
Supervisor (SUPERVISOR_ROUTER mode)
    ├── Account Performance IQ   → Lambda → Athena → S3
    ├── Support Signal IQ        → Lambda → Athena → S3
    ├── Product Usage IQ         → Lambda → Athena → S3
    └── Billing Pulse IQ         → Lambda → Athena → S3
```

The fix from this repo — imperative MANDATORY DEFAULTS phrasing — applies
*per specialist*. The supervisor is a pure router and stays thin.

## The four non-obvious IAM requirements

These bit us hard the first time we shipped Bedrock Agents to production.
All four are baked into `stacks/agent_stack.py`:

1. **Use `us.anthropic.claude-haiku-4-5-20251001-v1:0`, not `global.`** —
   the `global.` profile works with `bedrock:InvokeModel` directly but is
   rejected by Bedrock Agents at `CreateAgent` time. In `us-east-1` you can
   technically skip the profile and reference the foundation model
   directly, but the `us.*` profile works everywhere and keeps the pattern
   portable to regions where Haiku isn't native (ca-central-1, eu-*,
   ap-*).
2. **Agent execution role policies must be inline** (`inline_policies={...}`
   on the Role constructor), not `role.add_to_policy(...)`. The latter
   creates a separate `AWS::IAM::Policy` resource, and CloudFormation
   deploys it *after* the agent — `CreateAgent` validation fails before
   permissions attach.
3. **`aws-marketplace:Subscribe` / `ViewSubscriptions` / `Unsubscribe`**
   with an `aws:CalledViaLast = bedrock.amazonaws.com` condition. Bedrock
   completes a Marketplace subscription on the caller's behalf for Anthropic
   models; without this, runtime invokes return 403.
4. **Explicit model ARNs, not wildcards**. `bedrock:InvokeModel` must list
   both the inference-profile ARN and the underlying foundation-model ARN:
   ```
   arn:aws:bedrock:<region>:<account>:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0
   arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0
   ```
   Broad wildcards behave unreliably under Bedrock Agents IAM evaluation.

## License

MIT — do whatever you want with the code and the phrasings. If the
`MANDATORY DEFAULTS` pattern saves you a round-trip, that's the point.

## Author

[Curious Orbit](https://curiousorbit.com) — agentic AI specialists.
Talking Cloud podcast: [curiousorbit.com/podcast](https://curiousorbit.com/podcast).
