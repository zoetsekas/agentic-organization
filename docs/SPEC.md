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
| `decisions` | The decision vocabulary a mandate draws from (ADR-0065) |
| `separations` | Decisions no single agent may hold together (ADR-0070) |
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
| `missions` | Short-lived teams drawn from the organization (ADR-0039) |
| `model_policy` | Which models an agent may run on (ADR-0040) |
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

## Missions: short-lived teams

The org chart changes yearly and describes accountability. A mission changes
weekly and describes work (ADR-0039):

```yaml
missions:
  - id: q4_forecast_rebuild
    objective: Rebuild the Q4 forecast after the EMEA pipeline restatement.
    deliverables:
      - A restated Q4 forecast with the assumptions written down.
    status: active
    leader: cfo                       # must be one of the members
    members: [cfo, analyst, cro]      # drawn from the standing organization
    starts_on: "2026-09-15"
    ends_on: "2026-10-31"             # required: a mission always ends
    internal_delegation: true
```

Four rules are enforced: every mission has a **leader** who is a member; every
mission has an **end date** (one that never ends is a reorganization); roles
assigned to a mission are **intersected** with what each member already holds,
so a mission never grants access; and lateral reach never lets a member task
their own leader — though the mission leader may task members.

## Model policy

```yaml
model_policy:                          # the system default
  classes: [balanced]                  # frontier_reasoning | balanced | fast_cheap
  min_context_tokens: 100000           # long_context | vision | code | on_premises
  max_cost_per_million_tokens: 20
  require_regions: [eu-west]
  require_no_training_on_data: true
  subagent_classes: [fast_cheap]
```

Narrowed per agent. The spec names no model — it asks for a class and states
constraints. The **catalog** says which concrete models qualify, and the
compiler **refuses the build** if the binding picks one outside the policy,
listing what would work instead (ADR-0040):

```
$ orgagents compile … --target local
error: the bound model is not permitted for:
  analyst: 'claude-opus-5' costs 30.0 per million tokens, over the 20 ceiling
```

## The platform catalog

Separate from the spec: the governed inventory a designer chooses from
(ADR-0041). Models, MCP servers, plugins, tools, environment templates,
permission sets, guardrails and knowledge sources — each with an owner, an
approval status and entitlements.

```bash
orgagents catalogs models        # the approved model shelf, with cost and context
orgagents catalogs list --kind mcp_server
orgagents catalogs approve cat_model_self_hosted
```

Only `approved` and `restricted` entries are selectable; a `restricted` entry
needs a matching group entitlement; retiring one invalidates it in every future
design and the refusal names its replacement.

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

## Authority: what a unit may *decide*

Permission is whether the door opens. A **mandate** is whether you were the one
to open it. They are different questions and they fail differently: a missing
permission is a dead end, while a decision above your authority belongs to
somebody (ADR-0065).

```yaml
decisions:
  - {id: approve_invoice, title: Approve an invoice for payment}
  - {id: release_payment, title: Release a payment to the bank}

organization:
  id: northwind
  # No mandate here: the root holds the declared vocabulary. Enumerating it
  # would mean editing this list every time a leaf gains a function (ADR-0071).
  leader: ceo
  members:
    - id: ceo
      # A principal's authority is never silent. `[]` says it decides nothing.
      mandate: {decisions: []}
  teams:
    - id: treasury
      mandate:
        decisions: [release_payment]
```

Four rules carry most of the weight:

* **A team is a scope; an agent is a principal.** A unit's mandate bounds what
  its members may hold and nobody exercises it, so a team may hold both sides
  of a control while no agent may (ADR-0070).
* **Authority narrows downward.** An agent's effective mandate is the
  intersection with every unit above it, resolved once at the phase gate.
  Claiming what your line does not hold is an **error**, not a warning: an
  agent that silently decides nothing is the worst outcome.
* **Silence inherits.** An empty mandate means *the parent's*, never
  *unlimited* — with the root's leader the one principal that must declare.
* **Conditions are evaluated**, with a small grammar: `max_<field>`,
  `min_<field>`, `<field>_in`, checked against the arguments a call supplies. A
  condition naming a field the call omits is a refusal, and so is one this
  platform cannot parse.

## Separation of duties

```yaml
separations:
  - id: payment_control
    decisions: [raise_payment, approve_invoice, release_payment]
    reason: >-
      One principal that can raise an invoice, approve it and instruct the
      bank can pay a supplier that does not exist.
    enforcement:
      enforced_by: both
      authoritative: application
      enforced_in: the ERP's own segregation rules
```

Narrowing and separation are contradictory constraints — narrowing requires a
parent to hold the union of its children, separation requires that no principal
hold both sides — which is why the first applies to units and the second to
agents. A leader that inherits its unit's mandate into a violation must declare
a narrower one.

The check also runs against the **binding**: two decisions a rule keeps apart
may not resolve to one MCP server under one credential, because segregation is
enforced by the ERP and the bank, not by our mandate table.

## Autonomy: what an agent does without a person

Every capability declares how much of it runs unattended (ADR-0072):

| Posture | Means |
|---|---|
| `advisory` | Reads and models. **May not change a system of record.** |
| `human_decides` | Prepares and recommends; the decision is not the agent's. |
| `supervised` | Decides, and a person confirms before it takes effect. |
| `autonomous` | Decides and acts alone. |

The default is `advisory`, the most restrictive, so a mutation nobody has
thought about cannot ship. The gate checks the mechanics against the
declaration: autonomy needs a decision class the agent holds **and** evaluation
evidence behind it; supervision needs approval required and an approver who is
not the agent's own owner; `human_decides` needs the agent *not* to hold the
decision. An assignment may tighten a posture and never loosen it.

## Who enforces a control

An agentic organization does not replace its enterprise applications. The ERP
owns the ledger, the treasury system owns the sweep, the bank owns the payment.
So every control says who checks it (ADR-0073):

```yaml
enforcement:
  enforced_by: both          # platform | application | both
  authoritative: application  # required when both
  enforced_in: the treasury management system
  application_bounds: {max_facility_gbp: 25000000}
```

* `platform` — ours, and it **must be evaluable here** or the spec is refused.
  The default, so claiming a control obliges us to evaluate it.
* `application` — theirs. We may describe it and may not claim it.
* `both` — names an authoritative side, and is always reported.

Our bound may narrow what the application permits and never widen it. A bound
that reads as enforced and is not is worse than no bound.

## The fabric's house rules

`policies` below are a *design's* RBAC rules. A **platform policy** is a
different thing one layer up: what the fabric requires of any design before it
will build it (ADR-0076). It is owned by whoever runs the platform and is never
part of a spec — a design that could name the rules it is judged by is not
judged.

```yaml
# house.platform-policy.yaml — passed with --platform-policy
id: house
version: "1.0.0"
status: approved              # only an approved, current policy decides a build
approved_by: Security Engineering
approved_on: "2026-09-21"
review_interval_days: 180     # optional; where set, the approval lapses
treat_as: production          # strictness is ours, not the design's
require_declared: [separations, guardrails, decisions, evaluations]
forbid_autonomy_over: [release_payment, approve_capex]
severity:
  unused_capability: error    # raising needs no justification
```

Lowering a rule needs a `reasons` entry and is refused without one, and every
lowered rule is reported in the gate and stamped into the IR. A build with no
policy records `none` rather than looking the same as a policed one.

A policy has a lifecycle (ADR-0077). A **draft may be evaluated and may not
decide a build**, so an author can see what a rule does before asking anybody
to accept it. An approved policy without a signature and a date is refused at
load. Where `review_interval_days` is set, a lapsed approval stops deciding and
says by when it was due.

The stamp carries a **fingerprint over the substantive fields**, so
"this passed `house/1.0.0`" says *which* `house/1.0.0`: edit `treat_as` and keep
the version, and the fingerprint moves. Rewriting the description does not.

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

## Placements: where the work lives

An environment class is a *profile* — toolchain, tier, network posture,
egress. It says what an agent needs, not whose work it is, so keying sandbox
environments by it puts an HR agent and a Finance agent that both need
`analysis` in one place.

A **placement** is an org unit crossed with an environment class (ADR-0069).
It is the namespace model an enterprise already has: a name scope, a network
policy and storage; things inside share freely, things outside reach in only
over something declared.

```yaml
organization:
  id: northwind
  teams:
    - id: finance
      placement: true          # its own sandbox environment, volume, network
      teams:
        - id: controllership
          placement: true      # opts out of Finance's
        - id: tax              # declares nothing, so it is Finance's
```

**Opt-in, and it inherits.** A team that declares nothing sits in its nearest
declaring ancestor, and the root always declares, so every agent has exactly
one answer. Declaring nothing anywhere therefore gives the *widest*
arrangement — one place for the whole organization — which is what a design
gets by not deciding. The validator says so (`single_placement`) rather than
letting it pass quietly.

**What a placement gives its members.** One internal network, one shared
volume at `/srv/shared`, and one process namespace. Traffic between them is
permitted without a rule, which is a widening, so the generated README names
the co-resident agents.

**What the volume may carry is bounded and is never a grant.** It holds
exactly the data classes the unit's groups already share (`SharingScope.PROTECTED`).
An agent without a grant on a class does not acquire it by sharing a disk with
somebody who has one — the permission resolver still decides what may be
opened.

**Across placements, deny by default.** A path exists only where standing
structure put one: the manager chain (transitively — a network is not
transitive, so the closure is generated), declared peers, declared interaction
flows, shared services, and a channel with members on both sides. **A mission
grant never becomes a rule**, because a generated rule does not expire and a
mission window does; temporary reach travels over the bus, where the org-chart
check runs per message.

**A placement is not a security boundary.** The tenant is. Borrowing the
namespace model means borrowing its caveat — namespaces on an application
platform share a kernel, and so do these. Two further honest limits: generated
policy is a snapshot, so a reorganisation and the deployed rules disagree until
the next apply (reported as `placement_denies_delegation` when the design
already shows it); and two agents a separation rule keeps apart may still share
a process namespace, which is reported as `separated_agents_co_resident` and
may legitimately be accepted when the control is `authoritative: application`.

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

A pairing that names a declared person carries a reference instead of a copy:

```yaml
humans:
  - {person: p_director, roles: [owner]}
  - {person: p_controller, roles: [approver], approves: [pay_supplier]}
```

The inline form above stays valid, so a spec migrates one agent at a time.

## People are principals for authority, never for access

An agent has the whole apparatus — a role binding responsibilities to
capabilities and permissions, assignment that may narrow and never widen,
permissions resolved deny-by-default once at the phase gate, and mandates,
separations and autonomy postures above it. A person had a five-value pairing
enum. So a person's *relationship to an agent* was modelled and their
*position in the organization* was not (ADR-0079).

People are declared once, at the top level:

```yaml
people:
  - id: p_director
    name: Marcus Oyelaran
    contact: marcus.oyelaran@northwind.example
    position: Chief Financial Officer
    unit: finance                 # the org unit that bounds their authority
    mandate:
      decisions: [approve_invoice, close_period, file_tax_return]
```

Four things follow.

**Once, so one human is one principal.** Declared inline on each agent, the
same finance director appeared six times under six invented ids in the worked
example. Separation of duties cannot see past that, so it is refused: two
people sharing a contact address is an error, not a warning.

**A mandate, and never a capability or a permission.** Declaring
`capabilities` or `permissions` on a person is refused. We do not mediate a
person's access — they sign into the ERP under their employer's IAM — and a
permission this platform cannot enforce reads as a bound and is not one, which
is the rule [Who enforces a control](#who-enforces-a-control) applies to
controls, applied to principals. What *is* declared is what they may decide,
because this platform is what routes the escalation to them.

**Bounded by their unit.** A person's mandate narrows against the unit they
sit in, exactly as an agent's narrows against its team, and claiming past it
is `mandate_overreach`. A person attached to nothing sits under the root,
which is the widest bound the organization has and still a bound.

**Reachable, and checked.** The holder search tries the agents on a line first
and then the people attached to it, so an escalation prefers something that
can act here and reaches a human when nothing here holds the decision. A
decision only a person holds — capital allocation, which a board decides — is
held rather than reported as unheld, which is what let that work complete
inside the system at all. Separations run over people, and the four-eyes case
is checkable at last: **a person may not approve an action raised by an agent
they own.**

What is declared here is a **claim**. The real delegation of authority lives
in the organization's own approval matrix, which this platform does not read
and nothing reconciles. Authority is also attached to an individual rather
than a position, so it rots when they change jobs; positions would be right
and are an HR system we are not building (ADR-0079).

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
