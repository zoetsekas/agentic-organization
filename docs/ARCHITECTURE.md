# Architecture

This is the whole system in one document: what the planes are, what flows
between them, and where the load-bearing decisions sit. Every claim here is
anchored to an ADR; where the implementation is thinner than the design, this
document says so rather than describing the intent as if it were the state.

Diagrams are Mermaid and render on GitHub.

---

## 1. The three planes

The platform is not one application. It is three, with different jobs,
different access control and different failure domains (ADR-0049).

```mermaid
flowchart TB
    designers["Designers<br/>author organizations"]
    operators["Platform operators<br/>run the fabric"]
    people["Paired humans<br/>owners, approvers, reviewers"]

    subgraph DP["Designer plane — authoring"]
        canvas["Canvas and forms"]
        sdk["SDK"]
        spec["System Spec<br/>implementation-neutral"]
        catalog["Catalog of building blocks"]
        canvas --> spec
        sdk --> spec
        catalog -.offers.-> spec
    end

    subgraph FP["Fabric plane — control"]
        command["Command centre"]
        tenants["Tenant registry<br/>isolation domains"]
        compiler["Compiler<br/>spec + binding to IR"]
        deployments["Deployment lifecycle"]
        common["Common services<br/>catalog, observability, records, identity"]
        command --> tenants
        command --> deployments
        tenants --> compiler
        compiler --> deployments
    end

    subgraph TP["Tenant plane — workloads"]
        t1["Tenant A<br/>own infra, identities, data"]
        t2["Tenant B<br/>own infra, identities, data"]
    end

    designers --> canvas
    designers --> sdk
    operators --> command
    spec -- "publish is a request" --> tenants
    deployments -- generate --> t1
    deployments -- generate --> t2
    deployments -. "health, drift, quotas" .-> t1
    t1 -. "no path" .-x t2
    t1 --> people
    common -.-> t1
    common -.-> t2
```

Three rules make the separation structural rather than decorative:

- **A design is not a deployment.** Publishing from the designer is a request
  to the fabric, which decides whether, where and for whom it runs.
- **Operator and designer are different principals**, in both directions. An
  operator can stop, quarantine or re-deploy a tenant; it cannot edit the
  tenant's organization. A designer cannot see across the tenant boundary.
- **Isolation belongs to the fabric, not the design** (ADR-0050). A spec never
  declares its own tenancy, so it stays portable and a tenant cannot widen its
  own boundary by editing a design.

> **State of play.** The designer plane is built and tested. The fabric plane
> is recorded (ADR-0049/0050/0051) with the tenancy core and operational model
> under construction in WS-028 and WS-030. The command centre (WS-029) is not
> started. No tenant has ever been deployed from here: there is no cloud
> account and no container daemon in the development environment.

---

## 2. The spine: spec → IR → artifacts

Everything the platform does passes through one pipeline. The spec is
implementation-neutral; vendors appear only in the binding; permissions resolve
exactly once, into the IR; every target reads the IR and nothing else
(ADR-0002, ADR-0003, ADR-0005).

```mermaid
flowchart LR
    spec["System Spec<br/>no vendors, no tenancy"]
    binding["Binding<br/>target-scoped; vendors live here"]
    gate{"Phase gate<br/>definition complete?"}
    stop["Refused,<br/>naming what is missing"]
    tenant["Tenant<br/>namespace prefix"]
    ir["IR<br/>permissions, identities, org, missions,<br/>models — resolved once"]
    local["local<br/>Compose stack"]
    gcp["terraform:gcp"]
    aws["terraform:aws"]
    azure["terraform:azure"]
    runtime["Runtime loader<br/>a target, not the centre"]
    mapping["MAPPING.md<br/>every coarsened grant named"]

    spec --> gate
    binding --> gate
    gate -- no --> stop
    gate -- yes --> ir
    tenant --> ir
    ir --> local
    ir --> gcp
    ir --> aws
    ir --> azure
    ir --> runtime
    gcp --> mapping
    aws --> mapping
    azure --> mapping
```

Why it matters that resolution happens **once**: the Terraform a cloud applies
and the permissions the runtime enforces are derived from the same resolved
structure, so they cannot drift into disagreeing about who may do what. A test
asserts no target module imports `orgagents.spec`.

### The two phases

```mermaid
stateDiagram-v2
    [*] --> Definition
    Definition: Definition phase
    Definition: org, roles, permissions, data classes,<br/>environments, missions — no vendors
    Implementation: Implementation phase
    Implementation: binding picks runtimes, models, providers,<br/>regions, sinks
    Definition --> Definition: refine
    Definition --> Implementation: gate passes
    Implementation --> Definition: gate fails, with the reason
    Implementation --> Generated: compile
    Generated --> [*]
```

A definition that is incomplete cannot be compiled, and the refusal names what
is missing rather than emitting something half-bound (ADR-0019).

---

## 3. The organization model

Slow-changing structure, fast-changing work.

```mermaid
classDiagram
    class Organization {
        recursive tree of teams
    }
    class Team {
        +id
        +mandate
        +exactly one leader
        +roles
    }
    class Agent {
        +id
        +roles
        +capabilities
        +model policy
        +environment
    }
    class Mission {
        +objective
        +deliverables
        +leader
        +always an end date
    }
    class Role {
        +permissions
        +responsibilities
    }
    class HumanCounterpart {
        +contact
        +roles: owner, approver, reviewer
    }
    class SubAgent {
        acts as a tool
        narrow-only inheritance
    }

    Organization "1" o-- "*" Team
    Team "1" o-- "*" Team : child teams
    Team "1" *-- "*" Agent : members
    Team "1" --> "1" Agent : leader
    Agent "1" --> "*" Role
    Team "1" --> "*" Role
    Agent "1" --> "*" HumanCounterpart : paired with
    Agent "1" o-- "*" SubAgent
    Mission "1" --> "*" Agent : members drawn from the org
    Mission "1" --> "1" Agent : leader, must be a member
```

- **Every team has exactly one leader**, and a child team's leader participates
  in the parent implicitly through leadership rather than by double
  membership (ADR-0006).
- **A mission is a short-lived team** drawn from the standing organization,
  with an objective, deliverables and always an end date. Roles assigned to a
  mission are *intersected* with what members already hold, so a mission can
  never be a permission side-door, and lateral reach never lets someone task
  their own leader (ADR-0039).
- **Sub-agents are tools, not org members.** They have no reporting line, no
  human counterpart, no session and no memory of their own, and inherit only a
  narrowing of the parent's authority (ADR-0027).

### A mission's reach expires

```mermaid
sequenceDiagram
    participant A as Analyst
    participant G as Delegation gate
    participant M as Mission window
    A->>G: delegate to CRO
    G->>M: is the window open today?
    alt inside the window
        M-->>G: open
        G-->>A: allowed
    else past the end date
        M-->>G: closed
        G-->>A: refused
    end
    Note over G,M: Checked per call, not baked in at compile time,<br/>so an unswept mission still confers nothing.
```

---

## 4. Security: how a permission is decided

Deny by default, narrow-only inheritance, and an explicit guard rather than an
ambiguous one (ADR-0008).

```mermaid
flowchart TB
    ask["Agent asks to do X on Y"]
    tenantq{"Same tenant?"}
    denyT["Denied — absolute.<br/>No 'protected across tenants'"]
    collect["Collect grants:<br/>team roles inherited + agent roles + own"]
    matchq{"Any grant matches<br/>action and resource?"}
    denyN["Denied — nothing granted it"]
    condq{"Conditions hold?"}
    denyC["Denied — condition failed"]
    unlessq{"Any 'unless' guard fires?"}
    denyU["Denied — guard fired"]
    classq{"Data classification allows it?"}
    denyD["Denied — classification"]
    envq{"Environment and egress allow it?"}
    denyE["Denied — sandbox policy"]
    allow["Allowed, and recorded"]

    ask --> tenantq
    tenantq -- no --> denyT
    tenantq -- yes --> collect --> matchq
    matchq -- no --> denyN
    matchq -- yes --> condq
    condq -- no --> denyC
    condq -- yes --> unlessq
    unlessq -- yes --> denyU
    unlessq -- no --> classq
    classq -- no --> denyD
    classq -- yes --> envq
    envq -- no --> denyE
    envq -- yes --> allow
```

Inheritance only ever narrows: a child team cannot hold more than its parent,
and a mission cannot grant what a member lacked. Ambiguity on a deny fails
unsafe, which is why the `unless` guard is explicit (ADR-0008 v1.1.0).

---

## 5. An agent at runtime

What actually surrounds the model call.

```mermaid
flowchart TB
    subgraph Agent["One agent, one session"]
        prompt["Composed prompt<br/>role, responsibilities, org position,<br/>missions, shared instructions"]
        gin["Guardrails: input"]
        loop["Agent loop<br/>on an approved model"]
        gtin["Guardrails: tool input"]
        tools["Tools"]
        gtout["Guardrails: tool output"]
        contract["Output contract<br/>retry on violation, bounded"]
        gout["Guardrails: output"]
    end

    subgraph Harness["Harness — what the agent may reach"]
        mcp["MCP servers"]
        sql["Relational access<br/>policy-scoped"]
        skills["Skills and plugins"]
        endpoints["External agent endpoints"]
        sandbox["Sandbox environment<br/>tier, network, egress allowlist"]
    end

    subgraph Memory["Memory — two tiers"]
        session["Session memory<br/>always expires"]
        longterm["Long-term memory<br/>classified namespaces,<br/>governed promotion"]
    end

    subagents["Sub-agents as tools<br/>research, review"]
    delegate["Delegation<br/>reports, peers, live missions"]
    workspace["Artifact workspace<br/>offload large tool output"]

    prompt --> gin --> loop
    loop --> gtin --> tools --> gtout --> loop
    loop --> contract --> gout
    tools --- Harness
    loop --- Memory
    session -. promotion .-> longterm
    loop --> subagents
    loop --> delegate
    tools --> workspace
```

Guardrails run **outside** the agent loop, at four boundaries, so a compromised
prompt cannot argue its way past them (ADR-0035). Judgement at those boundaries
is pluggable: the deterministic pattern floor is the default, a model-backed
classifier can add findings but never clear one, and on a provider failure the
verdict degrades to the pattern floor and is marked degraded rather than
failing open to nothing (ADR-0045).

---

## 6. Data and memory

```mermaid
flowchart LR
    subgraph Classification["Classification governs placement, access and egress"]
        priv["private<br/>one agent"]
        prot["protected<br/>a group"]
        pub["public<br/>everyone in the tenant"]
    end
    subgraph Tiers["Memory tiers"]
        s["Session<br/>scoped to one session<br/>always has an expiry"]
        l["Long-term<br/>survives sessions<br/>same classification rules"]
    end
    promo{"Promotion<br/>governed, recorded"}
    s --> promo --> l
    priv --- s
    prot --- l
    pub --- l
    l -. "never crosses" .-x tenant["Another tenant"]
```

Memory namespaces reuse the classification rules rather than inventing a second
access model, so "what may this agent remember" and "what may this agent read"
are answered by the same engine (ADR-0028).

---

## 7. Tenancy and isolation

```mermaid
flowchart TB
    spec["One spec<br/>tenant-free, portable"]
    subgraph FabricAssign["Fabric assigns"]
        ta["Tenant A<br/>prefix acme-"]
        tb["Tenant B<br/>prefix globex-"]
    end
    subgraph A["Tenant A artifacts"]
        na["networks, volumes,<br/>identities, secret refs<br/>all acme- qualified"]
    end
    subgraph B["Tenant B artifacts"]
        nb["networks, volumes,<br/>identities, secret refs<br/>all globex- qualified"]
    end
    shared["Common services<br/>explicitly listed, audited"]

    spec --> ta --> na
    spec --> tb --> nb
    na -. "no shared name,<br/>volume, network or identity" .-x nb
    shared -.-> na
    shared -.-> nb
```

The same spec compiled for two tenants must share **no** identifier, volume,
network, identity or secret reference. What is shared between tenants is a
listed fabric decision — never an emergent consequence of naming.

> **The honest caveat.** A boundary is only as strong as the target enforces
> it. Where cloud IAM is coarser than the model, the generated grant is wider
> than ADR-0050 describes, and the mapping report has to say so in those words.
> And isolation nobody has attacked is a claim: generation tests prove
> artifacts differ, not that a breach fails. That test needs infrastructure
> this environment does not have — WS-028 M6.

---

## 8. Design to operation, end to end

```mermaid
sequenceDiagram
    actor D as Designer
    participant UI as Designer app
    participant F as Fabric
    participant C as Compiler
    participant T as Target
    actor O as Operator
    participant CC as Command centre

    D->>UI: draw and edit the organization
    UI->>UI: validate — least privilege, org rules, missions
    D->>UI: publish
    UI->>F: request deployment for a tenant
    O->>CC: review the request
    CC->>F: approve, assign isolation domain
    F->>C: compile for this tenant
    C-->>F: artifacts + mapping report
    F->>T: deploy
    T-->>F: state
    loop while running
        F->>T: observe
        T-->>F: health
        F->>F: compare with believed state
        F-->>CC: drift, quota, incident signals
    end
    O->>CC: stop / quarantine / re-quota / re-deploy
    Note over O,CC: An operator never edits the tenant's organization.<br/>That stays with the tenant's own designers.
```

### Deployment lifecycle

```mermaid
stateDiagram-v2
    [*] --> Requested
    Requested --> Generated: compile succeeds
    Requested --> Rejected: refused by an operator
    Generated --> Deployed: applied to a target
    Deployed --> Running: healthy
    Running --> Stopped: operator stops
    Running --> Quarantined: incident or breach suspicion
    Quarantined --> Running: cleared
    Stopped --> Running: restart
    Running --> Retired: decommissioned
    Stopped --> Retired
    Rejected --> [*]
    Retired --> [*]
```

Transitions are explicit and audited; an illegal transition is refused rather
than coerced.

---

## 9. Unattended work and reaching humans

```mermaid
flowchart LR
    trig["Triggers<br/>schedule, event, webhook, message"]
    sched["Scheduler<br/>cadence, overlap, catch-up, retry, halt"]
    run["Agent run<br/>owning agent's identity and permissions"]
    approve{"Approval required?"}
    route["Human routing<br/>availability, SLA, escalation"]
    chan["Channels<br/>Slack, Teams, email — abstract classes"]
    human["The right human"]
    proceed["Proceed"]

    trig --> sched --> run --> approve
    approve -- yes --> route --> chan --> human --> proceed
    approve -- no --> proceed
```

A scheduled run has exactly the owning agent's identity and permissions —
unattended work is not a way to acquire more. Channel *classes* live in the
spec; Slack and Teams appear only in the binding.

> Real Slack and Teams bridge clients are not built (WS-013 M4); routing plans
> are computed against an in-process bridge.

---

## 10. Governance: records as build artifacts

```mermaid
flowchart LR
    adr["ADR<br/>a decision"]
    ws["WS<br/>a workstream"]
    code["Code and tests"]
    idx["Generated indexes"]
    val["orgagents records validate"]

    adr -- "decides" --> ws
    ws -- "delivers" --> code
    code -- "verifies" --> adr
    adr --> idx
    ws --> idx
    adr --> val
    ws --> val
    val -- "dangling reference,<br/>version mismatch,<br/>broken supersession" --> fail["Build fails"]
```

Decisions are versioned, superseded symmetrically and machine-validated. A
decision that changes gets a version bump and a changelog row rather than a
quiet edit — the record of *why* survives the change.

---

## 11. Module map

```mermaid
flowchart TB
    subgraph Author["Authoring"]
        specm["spec/<br/>model, validate, loader,<br/>migrations, schema"]
        designer["designer/<br/>repository, rbac, locks,<br/>merge, audit, auth"]
        web["web/<br/>canvas, forms"]
    end
    subgraph Build["Build"]
        comp["compiler/<br/>ir, engine, registry"]
        targets["compiler/targets/<br/>local, terraform"]
        phases["phases.py"]
    end
    subgraph Fabric["Fabric"]
        fabricm["fabric/<br/>tenants, deployments,<br/>services, quotas, health"]
    end
    subgraph Run["Runtime and services"]
        rt["runtime/<br/>engine, loader, adapters,<br/>subagents, scheduler"]
        guard["guardrails.py<br/>classifiers.py"]
        mem["memory.py<br/>context.py"]
        org["org.py<br/>missions.py"]
        cat["catalogs/<br/>entries, service,<br/>sources, usage"]
        dirm["directory.py"]
    end
    store["store.py — JSON documents over SQLite"]
    records["records.py + docs/"]

    web --> designer --> specm
    specm --> phases --> comp --> targets
    comp --> rt
    fabricm --> comp
    rt --> guard
    rt --> mem
    rt --> org
    cat -.-> comp
    dirm -.-> specm
    designer --> store
    fabricm --> store
    rt --> store
    records -.-> Author
    records -.-> Build
    records -.-> Fabric
```

---

## 12. Where the design is ahead of the implementation

Kept here deliberately, so the diagrams above are not read as a description of
what runs today. The ordered list with owners is `docs/ROADMAP.md`.

| Area | Designed | Actually true today |
|---|---|---|
| Fabric plane | ADR-0049/0050/0051 | Tenancy core and operational model under construction; command centre not started |
| Tenant isolation | Absolute, fabric-assigned | Proven by generation tests only; no breach attempt, no running tenant |
| Cloud targets | Terraform for three providers | Generated and syntax-checked; never `terraform apply`-ed |
| Operations | Health, drift, quotas, incidents | Modelled against stubs; no adapter has met a real target |
| Guardrails | Pluggable judgement | Works; recall never measured against a labelled corpus |
| Designer identity | OIDC with group mapping | Works, but the JWT verification is hand-rolled RSA because no crypto library imports here — replace before production |
| Runtime adapters | Deep agents, OpenAI SDK, LangGraph | Thin bindings; only the `echo` adapter runs in CI |
| Human channels | Slack, Teams, approval routing | Routing computed; no real bridge client |
| Knowledge | Declared, governed sources | Not retrieved from |
