# What this target cannot enforce

Generated for `ayc`, target `acme:onprem`.

Acme's cluster schedules containers. It has no notion of a role, a permission,
a mandate or an approval, so none of the authority model survives into these
files — and saying otherwise would be the lie this page exists to prevent
(ADR-0073).

## This design

- **12** agents
- **12** agents with a mandate
- **29** resolved permissions
- **10** gated actions

## Construct by construct

| Construct | In this target | Enforced by |
|---|---|---|
| Instructions, model, tools | emitted | the agent container |
| Delegation edges | emitted (as gateway routes) | Acme's gateway, once configured |
| Roles and permissions | **no model** | the orgagents harness |
| Mandates | **no model** | the orgagents harness |
| Separation of duties | **no model** | the orgagents phase gate, at compile time |
| Approvals, and who may give them | **no model** | the orgagents harness |
| Sandboxes and network posture | **no model** | an infrastructure target |
| Data classes and egress | **no model** | the orgagents harness |

## What to do about it

Deploying these files gives Acme the agents and their wiring. It does not give
Acme the organization. Run the `local` or `terraform:*` target alongside if the
authority model needs to be held at run time rather than documented here.
