# Alpha release readiness — Docker

What exists, what is real, and what stands between here and a first alpha that
somebody else can run on their own machine with `docker compose up`.

Written 2026-09-20. **821 tests passing, 1 skipped. 90 records, 0 violations.**

The single fact that shapes this document: **no Docker daemon, no cloud
account, no provider credentials and no message broker have ever existed in the
environment this was built in.** Everything below is generated, parsed and
unit-tested. Nothing has been started. An alpha is the point at which that
stops being true.

---

## 1. Component status

Legend: **Built** — implemented and tested. **Contract** — modelled, tested
against a stub or fake, never met its real counterpart. **Partial** — usable
but knowingly incomplete. **Not started**.

### The three planes (ADR-0049)

| Component | State | Notes |
|---|---|---|
| Designer app (API, canvas, CLI) | Built | Runs; containerized (ADR-0048), image unbuilt |
| Designer backend (repos, RBAC, locks, merge) | Built | Three persistence backends |
| Designer identity — OIDC | Partial | Works, but **the JWT verification is hand-rolled RSA** because no crypto library imports here. Replace before anyone relies on it |
| Designer audit log | Built | Append-only, records refusals, all three backends |
| Command centre — backend and API | Built | `/api/fabric/*`, operator roles disjoint from designer roles |
| Command centre — front end | Built | 7 views, offline fixture mode, actions driven by the backend's `allowed_transitions` |
| Fabric: tenants and isolation domains | Built | Prefix validation, registry |
| Fabric: deployment lifecycle | Built | Role-keyed transition table, illegal moves refused |
| Fabric: quotas and entitlements | Built | Degrade between soft and hard; entitlement refuses outright (ADR-0052) |
| Fabric: health and drift | **Contract** | Stub backend only; no adapter has met a target |
| Fabric: common-service registry | Built | Each shared service names what crosses the boundary |
| Tenant plane | **Contract** | Generated per tenant; never deployed |

### Spec, compiler, targets

| Component | State | Notes |
|---|---|---|
| System Spec + validator | Built | spec_version 1.2.0; ~60 validation rules |
| Spec migrations + JSON Schema export | Built | Forward-only; one test skips for want of `jsonschema` |
| IR and permission resolution | Built | Resolved once; targets never import the spec |
| Local Docker target | Built | Per-tenant project, networks, volumes, sandbox images, Langflow, artifact store |
| **Compose validated by Docker's own parser** | Built | `docker compose config` runs without a daemon — a real gate |
| Terraform targets (GCP, AWS, Azure) | **Contract** | Generated and syntax-checked; never `terraform apply`-ed |
| Mapping reports | Built | Name what enforces each boundary and where it is coarser |
| Image pinning | Partial | 12 of 14 digests resolved against the real registry; Keycloak blocked by this proxy; mirroring not done |

### Runtime and agent behaviour

| Component | State | Notes |
|---|---|---|
| Org model, teams, roles, missions | Built | Mission reach expires per call |
| RBAC, data classification, sandbox classes | Built | Deny-by-default; narrow-only inheritance |
| Sandbox provider seam | Built | `container` real; `microvm_sbx` and `openshell` are **contracts** — no binary here |
| Memory (session + long-term) | Built | Recall is keyword-based |
| Sub-agents as tools | Built | Narrow-only inheritance |
| Guardrails | Partial | Pattern floor real; model classifier is a seam. **Recall never measured** |
| Output contracts + bounded retry | Built | |
| Scheduling and triggers | Built | |
| Workflow engines | Partial | `native` exercised; Langflow path exercised **through a fake transport**; LangGraph/LangChain/ADK are binding entries only |
| Human channels (Slack, Teams) | **Contract** | Routing computed against an in-process bridge; no real client |
| Agent-to-agent (A2A) | In progress | ADR-0058 accepted; implementation running |
| Message bus | In progress | ADR-0059 (NATS/JetStream per tenant); implementation running |
| Task intake | Built (port) | Port, local backend, conformance suite; **no real product adapter** |
| Knowledge / retrieval | Not started | Sources declared and governed, never retrieved from |
| Evaluations | Not started | Declared and never run — so nothing learns |

### Runtime adapters

| Adapter | State |
|---|---|
| `echo` | Built — the only one exercised in CI |
| deep agents, OpenAI Agents SDK, LangGraph | **Contract** — thin, lazily imported, need their dependency and credentials |

---

## 2. What an alpha has to mean

An alpha is not "more features". It is the first build where the claims are
**true on somebody else's machine**. Three properties:

1. `docker compose up` stands up the designer, and a person can design a
   system in it.
2. That design compiles to a Docker stack that **actually starts**, and an
   agent in it does a piece of work end to end.
3. Everything the documentation claims about isolation is either demonstrated
   or explicitly labelled as unproven.

---

## 3. Pending for alpha

### Blocking — nothing ships without these

| # | Item | Why it blocks | Owner |
|---|---|---|---|
| A1 | **Build the images and run the designer stack** on a machine with a daemon | Every image in this repo is unbuilt. The first `docker build` will fail at something; it always does | ADR-0048, WS-006 |
| A2 | **Start a generated tenant stack and run one agent end to end** | The local target has never been executed. This is the single largest unknown in the project | WS-006 M4 |
| A3 | **Replace the hand-rolled JWT verification** with a vetted library | Hand-written RSA in an auth path. Not shippable, even in alpha | WS-021 M3 |
| A4 | **Resolve or replace the remaining unpinned images** | Keycloak is unresolved because quay.io is blocked here; mirroring (ADR-0053 rule 2) has not happened at all | ADR-0053 |
| A5 | **One real runtime adapter working against a live model** | Only `echo` runs. An alpha where no agent can think is a demo of a compiler | WS-008 M3 |
| A6 | **A smoke test in CI** that builds the images and starts the stack | Otherwise A1 and A2 regress the day after they are fixed | WS-006 M4 |

### Should-have — an alpha is embarrassing without them

| # | Item | Why |
|---|---|---|
| B1 | Wire the runtime to the NATS bus once the adapter lands | Containerized agents otherwise cannot reach each other |
| B2 | One real human channel (Slack **or** Teams) | Approval routing is currently theatre without it; OpenClaw's gateway is the candidate |
| B3 | Health/drift adapter against the Docker target | The command centre shows a stub's opinion |
| B4 | Retention and redaction policy for the audit log | It records people indefinitely with no policy |
| ~~B5~~ | ~~`jsonschema` as a dev dependency~~ **done** | Un-skipping it found a real bug: the exported schema rejected our own worked example. Fixed |
| B6 | Divergence signals routed somewhere | Task divergence is computed on demand and stored nowhere |

### Explicitly **not** in alpha — say so, do not imply otherwise

- Cloud targets. Generated, never applied. Alpha is Docker.
- A verified tenant breach attempt (WS-028 M6). Isolation is proven by
  generation tests only, and **isolation nobody has attacked is a claim**.
- `microvm_sbx` / `openshell` sandbox providers. Seams with no binary behind
  them; alpha runs on `container`, whose boundary is a shared host kernel.
- A real task-service adapter. The port exists; the product choice is
  deliberately deferred (ADR-0057).
- Inbound A2A — serving our own agent card. Default is none (WS-019 M5).
- Evaluations, retrieval, learning. Not started, and the cognitive gap in
  LANDSCAPE §7 is not an alpha problem.

---

## 4. The honest summary

The **design** is further along than the **deployment** by a wide margin: 90
governance records, 821 tests, four compile targets, three planes — and not one
container has ever started. That is a real risk and it is concentrated in A1
and A2. Everything else on the blocking list is a day or two of work; those two
are the ones that will find out what is actually wrong.

The second risk is quieter. Several components are **contracts tested against
fakes** — health, drift, Slack, Teams, the cloud targets, two sandbox
providers, three runtime adapters, three workflow engines. Each is honest in
isolation. Together they mean the proportion of this system that has met its
real counterpart is smaller than the test count suggests, and an alpha is where
that gets corrected rather than compounded.

---

## 5. Running it (added after the bring-up path was written)

```bash
make check        # records, spec and phase gate — no daemon needed
make config       # docker parses every generated Compose file — no daemon needed
make alpha        # build, start, run one agent, report          — needs a daemon
```

`make alpha` is `check → config → build → up → smoke`. `scripts/smoke.py` is
the honest part: it runs the nine steps below and says which one failed, on the
grounds that the useful information on a first run is not "it broke" but "it
got this far".

1. docker is available
2. the spec validates
3. the system compiles for the local target
4. docker parses every generated Compose file
5. the designer image builds
6. the designer starts and answers `/healthz`
7. the designer UI is served
8. the demo organization seeds and **an agent runs**
9. the tenant stack starts

Useful flags: `--keep` leaves the stacks up for poking at, `--skip-tenant`
stops after the designer. `make down` stops everything this repository starts.

**Expect step 5 or 6 to fail the first time.** No image in this repository has
ever been built. That is what these steps are for.
