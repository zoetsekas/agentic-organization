---
id: ADR-0112
title: Everything the platform models is in a UML profile, and there is one profile per concern
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Designer, Compiler, Runtime, Security, Data Governance]
informed: [All engineering]
scope: [spec, compiler, targets, ui, docs]
workstreams: [WS-032]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0034, ADR-0085, ADR-0099, ADR-0101, ADR-0102, ADR-0103, ADR-0104, ADR-0110, ADR-0111]
tags: [uml, metamodel, profile, completeness, binding, deployment]
---

# ADR-0112: Everything the platform models is in a UML profile, and there is one profile per concern

## Context
ADR-0101 made UML the metamodel and the platform's kinds a profile over it:
34 stereotypes and 53 relationships in `orgagents.metamodel`, from which the
designer's link rules, the canvas and the tests are derived. It covered the
*palette kinds* — the things a person drags onto a canvas — and stopped
there.

Measured against the models themselves (every Pydantic class and enumeration
reachable from `SystemSpec` and from `Binding`), the profile is far from
complete:

| Model | Classes | Not in the profile | Enumerations | Not in the profile |
|---|---|---|---|---|
| System spec | 61 | 27 | 38 | 35 |
| Binding | 14 | 14 | — | — |

Outside it are things that carry governance weight: `Mandate`, `Permission`,
`CapabilityConstraint`, `ControlEnforcement`, `PromotionGate`, `Budget`,
`ModelPolicy`, `Compliance`, `DataRelation`, `EscalationStep`, the activity
graph itself, every enumeration (`SharingScope`, `NetworkPosture`,
`AutonomyPosture`, `Effect`, ...), and the whole binding — servers, targets,
runtimes, capability and workflow bindings — which is the deployment half of
every design. A concept outside the profile is invisible to the derived
machinery: it is not drawn, not filtered, not checked by the metamodel
constraints, not traced by the transformation (ADR-0104), and documented only
where somebody remembered to.

## Decision
**Everything the platform models is declared in a UML profile.** "Everything"
is checkable: every class and enumeration reachable from `SystemSpec` or
`Binding`, and every field on them. Nothing is exempt by being small.

1. **Every class is a stereotype or a DataType.** A thing with identity that a
   person designs (an agent, a mandate, a server) is a stereotype extending a
   UML metaclass. A value with no identity of its own (a `Budget`, a
   `WorkingHours`, a `ScalingPolicy`) is a UML **DataType** in the profile,
   with its properties typed.
2. **Every enumeration is a UML Enumeration** in the profile, with its
   literals, so a property typed by it is typed in the model and not just in
   Python.
3. **Every field is a Property or a relationship end.** A field holding
   another element's id is a declared relationship (association, composition,
   dependency, deployment, realization, generalization) with multiplicities; a
   field holding a value is an attribute with a type and a multiplicity. A
   field that is neither is a modelling error the test reports.
4. **The binding is modelled too, as UML deployment.** Servers are Nodes or
   ExecutionEnvironments, a target is a deployment specification, and a
   capability binding is a Deployment/Manifestation from the capability to the
   server that realises it. The spec's neutrality (ADR-0004) is kept by
   putting these in their own profile that the spec profiles do not import.
5. **One profile per concern, with explicit imports.** A single profile of a
   hundred-plus elements is unreadable, so the model is split:

   | Profile | Holds | Imports |
   |---|---|---|
   | **Core** | Model, Metadata, Comment/Note, the shared enumerations (`SharingScope`, `Effect`, ...) | UML |
   | **Organisation** | Organisation, Team, Agent, Sub-agent, Person, Role, Mission; succession, scaling, working hours | Core |
   | **Authority** | Decision, Mandate, Separation, Policy, Permission, ControlEnforcement, autonomy posture | Core, Organisation |
   | **Access** | Capability, CapabilityConstraint, Endpoint, Environment, network posture, toolchain classes | Core, Organisation |
   | **Data** | DataClass, DataRelation (ADR-0111), DataDependency, semantics, staleness, ArtifactStore | Core, Organisation, Access |
   | **Knowledge & tools** | Knowledge, Memory and its policies, Skill, Plugin, Tool, context policy | Core, Organisation, Data |
   | **Process** | Workflow (Activity), its action kinds as stereotypes of UML actions, fork/join (ADR-0110), WorkflowInterface, Trigger, Channel, escalation | Core, Organisation, Authority, Data |
   | **Assurance** | Guardrail, OutputContract, Evaluation, PromotionGate, Compliance, Observability, Resilience, Budget, ModelPolicy, Lifecycle | Core, Organisation, Data |
   | **Deployment** | Binding, Server, Target, Runtime, Infrastructure, and one binding per spec concern | every profile above; nothing imports it |

   A stereotype belongs to exactly one profile. A relationship belongs to the
   profile of its owning end. An import cycle is an error.
6. **Diagrams are views of profiles.** Each diagram aspect (ADR-0100) names
   the profiles it draws from: Organisation draws Organisation, Authority and
   Access; Process draws Process; Data draws Data (ADR-0111); a new
   **Deployment** aspect draws the binding. The palette shows the stereotypes
   of the profiles the open diagram draws.
7. **Layout stays out.** Where a box sits is diagram interchange, not model
   (ADR-0034), and is not part of any profile.

## Scope
`orgagents.metamodel` (split into a package of profiles), the link rules and
constraints derived from it, the transformation trace (ADR-0104), the designer
palette and diagram aspects, the issue catalog and the documentation. The spec
and binding *file formats* do not change: this declares what they already
mean. Designer-application records (workspaces, locks, audit) are platform
state, not design, and are out of scope.

## Implementation
- `orgagents/metamodel/` becomes a package: `core.py`, `organisation.py`,
  `authority.py`, `access.py`, `data.py`, `knowledge.py`, `process.py`,
  `assurance.py`, `deployment.py`, each declaring its stereotypes, DataTypes,
  Enumerations and relationships, and its imports. `metamodel/__init__.py`
  assembles them and keeps today's public API (`link_rules()`, the
  constraints) unchanged, so no caller moves.
- **Completeness test** (the enforcement of this ADR): walk every class and
  enumeration reachable from `SystemSpec` and `Binding`; each must be declared
  in exactly one profile, and each field must be a declared property or
  relationship end. The test prints the gap, so it doubles as the migration
  checklist.
- **Profile integrity tests:** no stereotype in two profiles; imports acyclic;
  no spec profile imports Deployment; every relationship's ends are visible
  from its profile.
- A generated **profile reference** (`docs/metamodel/`) — one page per profile
  with its class diagram — produced from the declarations, so documentation
  cannot drift.
- Designer: palette grouped by profile; diagram aspects declare their
  profiles; a Deployment aspect over the binding.

Order of work: split the existing 34 stereotypes and 53 relationships into
profiles with no behaviour change; add the completeness test with the current
gap as an explicit, shrinking allow-list; close the gap concern by concern
(Authority and Data first, because they carry restrictions; Deployment last);
remove the allow-list.

## Timeline
After ADR-0110 Milestone 1. ADR-0111 (data relations and the Data diagram)
lands inside this as the Data profile's first slice.

## Advantages
- One definition of what the platform means, from which drawing, checking,
  tracing and documentation are all derived — including the parts nobody
  drags onto a canvas.
- A concept cannot be added to the spec without being modelled: the
  completeness test fails first.
- Profiles give each reviewer — organisation, security, data, operations —
  a model of their concern that fits on a page.
- The binding becomes a first-class deployment model instead of a file only
  the compiler reads.

## Disadvantages
- A large one-off migration: roughly 27 classes, 35 enumerations and all 14
  binding classes to declare, plus every field.
- Every future spec change costs a profile change. That is the point, but it
  is a cost.
- Nine profiles are more to navigate than one; the generated reference and
  the per-aspect palette are what keep this usable.

## Alternatives considered
- **Keep one profile and add the missing elements to it** — rejected: over a
  hundred elements in one declaration is unreadable, and there is no place to
  keep the spec's neutrality from the binding.
- **Model only what is drawn** (ADR-0101's scope) — rejected by this ADR: the
  undrawn parts (mandates, permissions, gates, bindings) are where most of
  the governance lives.
- **Generate the profile from the Pydantic models** — rejected: it would
  declare Python's structure, not the UML relationship kinds, multiplicities
  and owning ends that give the model its meaning. The completeness test
  compares the two instead.

## Verification
The completeness test passes with an empty allow-list; the profile integrity
tests pass; `link_rules()` output is unchanged by the split; the generated
profile reference exists for all nine profiles; and the designer shows a
Deployment diagram for the AYC binding.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
