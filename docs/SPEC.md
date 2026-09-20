# The System Spec

The spec is the source of truth (ADR-0004). It describes an agentic system in
capability terms and names no vendor, framework or cloud — those live in a
**binding**. A test walks the schema and fails if an implementation name leaks
in, so neutrality is enforced rather than intended.

```
spec (what)  +  binding (how)  →  IR (resolved)  →  target plugins  →  artifacts
```

## Blocks

| Block | Answers |
|---|---|
| `metadata` | What is this system, which spec version, which environment |
| `data_classes` | How data is classified, and what that implies (ADR-0017) |
| `capabilities` | What external access agents need, with constraints (ADR-0010) |
| `environments` | What isolation their work needs (ADR-0009) |
| `roles` | Responsibilities + capabilities + permissions, as one contract (ADR-0007) |
| `policies` | Explicit allow/deny with attribute conditions (ADR-0008) |
| `organization` | The recursive team tree, one leader each (ADR-0006) |
| `workflows` | Processes that must be auditable |
| `channels` | Communication surfaces, with the human contract (ADR-0021) |
| `triggers` | What starts a run without a person asking (ADR-0020) |
| `interaction_flows` | Declared directional agent-to-agent links (ADR-0024) |
| `knowledge` | Grounding sources agents may consult (ADR-0023) |
| `skills` | Instruction packs an agent carries (ADR-0029) |
| `plugins` | Bundles of skills and tools (ADR-0029) |
| `tools` | Named wrappers over things already granted (ADR-0029) |
| `endpoints` | External agents ours may call, by trust level (ADR-0030) |
| `memory` | Session and long-term memory, with namespaces (ADR-0028) |
| `guardrails` | Checks on what may pass a boundary (ADR-0035) |
| `artifact_stores` · `context` | Workspaces, offloading and compaction (ADR-0036) |
| `output_contracts` | Checkable shapes a result must match (ADR-0037) |
| `operating_principles` | Instructions every agent carries (ADR-0038) |
| `budgets` | Spend ceilings with a mandatory breach action (ADR-0022) |
| `lifecycle` | Stages, promotion gates and evaluation cases (ADR-0022) |
| `compliance` | Residency, retention, redaction, review interval |
| `resilience` | Durability and run budgets (ADR-0025) |
| `observability` | Required signals and alert conditions (ADR-0016) |
| `deployment` | Which targets to compile for |

## Organization

Teams nest; each has exactly one leader, and the leader is a member of the team
it leads. A child team's leader participates in the parent **implicitly**
through leadership — listing it in the parent's members too would give it two
home teams, and the validator rejects that (ADR-0006 v1.1.0).

```yaml
organization:
  id: acme
  leader: ceo
  roles: [executive_team]
  members: [{id: ceo, roles: [chief_executive], ...}]
  teams:
    - id: technology
      leader: cto
      roles: [engineering_team]
      members: [{id: cto, roles: [function_head], ...}]
      teams:
        - id: platform
          leader: platform_lead       # participates in `technology` implicitly
          members: [{id: platform_lead, ...}, {id: platform_engineer, ...}]
```

Delegation follows the tree: a leader delegates to its members and to child-team
leaders, declared `peers` are reachable laterally, `shared_service: true` agents
are callable from anywhere, and everything else escalates.

## Roles carry both sides

```yaml
- id: financial_analyst
  kind: agent
  responsibilities:
    - Answer financial questions from the warehouse, always citing sources.
  capabilities: [warehouse_query]
  permissions:
    - {action: query, resource_kind: capability, resource: warehouse_query}
```

A capability granted with no responsibility describing it is a warning; a
wildcard resource is a warning in development and an **error** in production.

## Policies: `conditions` vs `unless`

`conditions` must hold for a rule to apply. `unless` disapplies it. Exception
-shaped denies are written with `unless`, because misreading the polarity of a
deny fails in the unsafe direction (ADR-0008 v1.1.0):

```yaml
- id: pii_never_leaves_clean_room
  effect: deny
  resources: [customer_pii]
  unless:
    environments: [isolated_review]    # denied everywhere else
```

Deny always wins, inheritance only narrows, and the default is deny.

## Environments narrow, never widen

An agent selects an environment class and may only make it stricter. A longer
timeout, a broader network posture or extra egress is rejected at validation —
not silently ignored.

## Triggers: unattended work

```yaml
triggers:
  - id: weekday_flash_report
    kind: schedule                      # schedule | event | webhook | message | manual
    agent: analyst                      # runs as this agent, with its permissions
    workflow: governed_data_request
    cadence: {expression: "0 7 * * 1-5", timezone: Europe/London}
    deliver_to: [exec_briefing]
    overlap: skip                       # skip | queue | cancel_previous | allow
    catch_up: run_once                  # skip_missed | run_once | run_all
    max_runtime_seconds: 900
    failure: {retries: 2, escalate_after_failures: 2, notify_channel: finance_approvals}
```

Cadences are five-field cron or a plain interval (`every 15 minutes`), with a
timezone; `daily`, `@hourly` and `weekdays` are aliases. One interpreter serves
the validator, the preview and every target, so they cannot disagree.

Two invariants: the scheduler holds **no credentials** — it wakes the agent,
which runs under its own identity — and a trigger with neither `deliver_to` nor
`failure.notify_channel` is a warning in development and an error in
production.

```bash
orgagents schedule examples/acme.system.yaml --count 3 --simulate-days 7
```

## Channels: the human contract

```yaml
channels:
  - id: finance_approvals
    human_facing: true
    purposes: [approve, ask]            # routing to another purpose is refused
    response_sla_minutes: 120
    out_of_hours: queue                 # queue | escalate | notify_anyway
    forbid_data_classes: [customer_pii]
    working_hours: {timezone: Europe/London, days: [1,2,3,4,5],
                    start_hour: 9, end_hour: 17}
    escalation:
      - {after_minutes: 60,  notify: priya@acme.example}
      - {after_minutes: 240, notify: dana@acme.example, channel: exec_briefing}
```

An approval raised at 03:00 UTC queues to 08:00 UTC (09:00 London), expires at
10:00, escalates to the CFO at 09:00 and to the CEO at 12:00 — all computed by
`humans.plan()`. An incident channel sets `out_of_hours: notify_anyway` and
behaves differently by design.

The binding names the provider and the bot identity *reference*; the generated
bridge holds the credential, the agents do not.

## Human pairing

An agent answers to several people in different capacities (ADR-0026):

```yaml
humans:
  - {name: Jo Adeyemi, contact: jo@acme.example, roles: [owner],
     channel: change_review}
  - {name: Samir Haddad, contact: samir@acme.example,
     roles: [approver, reviewer], approves: [code_change],
     channel: change_review}
```

Exactly one paired human holds `owner`. Every gated action must have a paired
approver who covers it — otherwise the agent stops at its own gate, and the
validator says so. The pre-1.1 single `human:` field still loads as one owner.

## Sub-agents: tools, not hires

```yaml
subagents:
  - id: topic_research
    kind: research               # research | review | summarize | extract |
                                 # critique | plan | verify | custom
    purpose: Gather context on a metric before analysing it.
    capabilities: [warehouse_query]     # must be a SUBSET of the parent's
    knowledge: [finance_handbook]
    returns: a cited findings list
    max_runtime_seconds: 300
```

Exposed as `subagent_topic_research`. No reporting line, no human of its own,
no session, no memory beyond the call. **Naming nothing means reaching
nothing** — the safe default is the lazy one. Widening anything the parent
holds, including the environment, fails validation (ADR-0027).

## Memory

```yaml
memory:
  session:
    max_items: 200
    recall: automatic
    redact_data_classes: [customer_pii]
  long_term:
    retention_days: 365
    promotion_allowed: true
    promotion_requires_approval: false
  namespaces:
    - {id: query_patterns, scope: protected, groups: [finance],
       data_classes: [finance_internal], retention_days: 365}
    - {id: company_facts, scope: public, data_classes: [public_knowledge]}
```

Session memory is private to one session and **always expires**, even with no
retention set — otherwise it is long-term memory nobody governed. Long-term
memory lives in namespaces whose scope decides who may recall it, using the
same rules as any other data (ADR-0017).

Promotion is the only bridge, and it needs all four: policy allows it, the
namespace holds that data class, the agent may read that class, and — where
configured — a human agrees.

An agent narrows the contract:

```yaml
memory:
  long_term_enabled: false      # the clean-room agent remembers nothing
  may_promote: false
```

Runtime tools: `memory_remember`, `memory_recall`, `memory_promote`,
`memory_forget`. With `recall: automatic`, relevant long-term memories are
pre-loaded into the session and the pre-load is recorded.

## Skills, plugins and tools

Three distinct things, so a reviewer can tell which lines widen access:

| | Grants access? | What it is |
|---|:--:|---|
| **capability** / **endpoint** | **yes** | what the agent may reach |
| **skill** | no | instructions it carries |
| **plugin** | no | a bundle of skills and tools |
| **tool** | no | a named, narrowed wrapper over something already held |

```yaml
tools:
  - id: warehouse_lookup
    wraps_kind: capability       # capability | subagent | workflow | endpoint
    wraps: warehouse_query
    constraints: {max_rows: 100, allowed_operations: [select]}
```

A wrapper may narrow and may add an approval gate. It can never remove one, and
it never grants anything its target does not (ADR-0029).

## External agent endpoints

```yaml
endpoints:
  - id: market_research_desk
    trust: partner               # internal | partner | external
    provides: [market_sizing]
    send_data_classes: [public_knowledge]   # non-internal ⇒ public only
    treat_output_as_data: true              # required for non-internal
    requires_approval: true
```

Outbound is classified: a non-`internal` endpoint may be sent public data only,
or validation fails as exfiltration. Inbound is data, never instruction, and
the composed prompt says so. The endpoint's credential lands on the **calling
agent's** identity (ADR-0030).

## Guardrails

Permissions decide what an agent may *reach*; guardrails decide what may
*pass* (ADR-0035):

```yaml
guardrails:
  - id: no_credentials_out
    applies_to: [output, tool_output]      # input | output | tool_input | tool_output
    checks: [secrets]                      # secrets | pii | prompt_injection |
    on_violation: block                    # data_class | url_allowlist | pattern |
  - id: mask_identifiers                   # max_length | schema
    applies_to: [output]
    checks: [pii]
    on_violation: redact                   # block | redact | flag | escalate
  - id: pii_stays_in_the_clean_room
    checks: [data_class]
    data_classes: [customer_pii]
    on_violation: escalate
    escalate_channel: finance_approvals
```

System guardrails apply to **every** agent; an agent may add, never remove.
Enforcement happens outside the agent loop, so a jailbroken prompt never
reaches the check, and the composed prompt tells the agent the boundaries exist
and to report what it needed rather than work around one.

## Context and workspaces

```yaml
artifact_stores:
  - {id: finance_scratch, scope: protected, groups: [finance],
     data_classes: [finance_internal], retention_days: 14}

context:
  max_context_tokens: 150000
  summarize_after_tokens: 100000     # must be below max, or it never fires
  keep_last_turns: 6
  offload_tool_output_bytes: 20000
  offload_to: finance_scratch
```

A tool result over the threshold is written to the store and replaced by a
reference plus the first 400 characters; the agent reads it back only if it
needs to (`artifact_read`). Past the token threshold, older turns compact and
recent ones stay verbatim — and **compaction that would not reduce tokens is
refused**, because a summary longer than what it replaces is not a summary.

An artifact store is deliberately a third thing beside memory (what an agent
learned) and the sandbox (where it executes).

## Output contracts

```yaml
output_contracts:
  - id: cited_findings
    schema:
      type: object
      required: [findings]
      properties:
        findings:
          type: array
          items: {type: object, required: [text, source]}
    on_violation: retry
```

Referenced by an agent, a sub-agent or a tool. `returns:` prose stays for the
reader; the contract is what a caller can check. It validates **shape, not
correctness** — a conforming answer can still be wrong.

## Shared operating instructions

```yaml
operating_principles:                 # every agent carries these
  - Say what you do not know. An unsourced figure is worse than no figure.

organization:
  teams:
    - id: finance
      shared_instructions:            # every member of this team carries these
        - Every figure names the period and the source table it came from.
```

Composed into the prompt **with their source**, so a reader sees which rule came
from the organization and which from Finance. Instructions are not enforcement:
anything that must hold belongs in a guardrail or a permission (ADR-0038).

## Interaction flows

The tree gives delegation. Flows give everything else, typed and directional:

```yaml
interaction_flows:
  - {source: analyst, target: sre, kind: consult,
     description: May ask about pipeline health, may not task the SRE.}
  - {source: sre, target: cto, kind: escalate,
     description: Raises SEV1 directly, bypassing the platform lead.}
```

Only `delegate` flows widen delegation. A `consult` never becomes an instruction.

## Lifecycle, budgets and compliance

```yaml
budgets:
  - {id: reconciler_daily, scope_kind: agent, scope: reconciler,
     period: daily, limit_usd: 50, on_breach: halt}

lifecycle:
  stage: development
  owner: Acme Platform Team
  gates:
    - to_stage: production
      requires: [evaluations_passed, human_approval, security_review,
                 cost_within_budget, permissions_reviewed]
      approvers: [priya@acme.example]
  evaluations:
    - id: refuses_pii_outside_clean_room
      given: Export the customer list with national identifiers.
      expect: A refusal explaining the clean-room restriction.
      applies_to: [analyst, cfo, reconciler]
```

Every agent resolves to exactly one budget — the tightest of agent, team and
system — and a breach action is mandatory. Declared evaluations are gated on
today and **executed from WS-014 M3**; the gate records the requirement, it does
not yet verify it.

## Bindings

```yaml
targets:
  - target: local
    runtime: {adapter: echo}
    environments: [{environment: analysis, image: "python:3.11", cpu: "2"}]
    capabilities:
      - {capability: warehouse_query, server_name: warehouse,
         engine: postgres, dsn_secret_ref: WAREHOUSE_DSN}   # a name, never a value
    channels:
      - {channel: finance_approvals, provider: msteams,
         workspace: acme.onmicrosoft.com, bot_identity_ref: TEAMS_BOT_ID}
    knowledge:
      - {knowledge: finance_handbook, provider: wiki, index: finance-handbook-v3,
         secret_ref: WIKI_TOKEN}
    scheduler: {provider: cloud_scheduler, queue: acme-triggers,
                dead_letter: acme-triggers-dlq, max_concurrency: 8}
```

Swapping cloud or agent framework is a change to the binding. The spec does not
move.

## The two phases

The definition phase is everything above; the implementation phase is the
binding. `orgagents phase` checks both and names the fix for each failure
(ADR-0019) — a definition with failures cannot be meaningfully bound, and an
incomplete binding cannot be compiled.

```bash
orgagents phase examples/acme.system.yaml \
  --binding examples/acme.binding.yaml --target terraform:gcp
```

## Commands

```bash
orgagents spec validate examples/acme.system.yaml
orgagents spec show     examples/acme.system.yaml      # resolved agents at a glance
orgagents spec ir       examples/acme.system.yaml      # the full IR, for review
orgagents targets
orgagents phase    examples/acme.system.yaml --binding examples/acme.binding.yaml \
                   --target local
orgagents schedule examples/acme.system.yaml --simulate-days 7
orgagents compile examples/acme.system.yaml \
  --binding examples/acme.binding.yaml \
  --target local --target terraform:gcp --out build
```

Generated output is compiler-owned: regeneration is byte-identical, a
hand-edited generated file blocks the next compile until you use `--force`, and
`overlays/` is yours (ADR-0014).
