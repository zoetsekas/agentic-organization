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
| `channels` | Abstract communication surfaces |
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

## Bindings

```yaml
targets:
  - target: local
    runtime: {adapter: echo}
    environments: [{environment: analysis, image: "python:3.11", cpu: "2"}]
    capabilities:
      - {capability: warehouse_query, server_name: warehouse,
         engine: postgres, dsn_secret_ref: WAREHOUSE_DSN}   # a name, never a value
```

Swapping cloud or agent framework is a change to the binding. The spec does not
move.

## Commands

```bash
orgagents spec validate examples/acme.system.yaml
orgagents spec show     examples/acme.system.yaml      # resolved agents at a glance
orgagents spec ir       examples/acme.system.yaml      # the full IR, for review
orgagents targets
orgagents compile examples/acme.system.yaml \
  --binding examples/acme.binding.yaml \
  --target local --target terraform:gcp --out build
```

Generated output is compiler-owned: regeneration is byte-identical, a
hand-edited generated file blocks the next compile until you use `--force`, and
`overlays/` is yours (ADR-0014).
