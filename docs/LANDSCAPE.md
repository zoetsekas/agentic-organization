# Landscape: what comparable systems do, and what we take from them

Researched September 2026. The question this document answers is not "what
exists" but **which capabilities our designer must have to be credible in an
enterprise**, and which we deliberately decline.

Sources are linked inline and listed at the end. One caveat: the brief also
named "here message", which we could not identify as a product — no matching
platform surfaced in search. If it is an internal tool or a name we misread,
the gap analysis below should be re-run against it.

---

## 1. What each system is actually good at

### OpenClaw — the channel gateway pattern
A self-hosted personal agent platform whose central idea is a **Gateway**: one
control-plane process that bridges an agent to the messaging surfaces people
already live in (Discord, iMessage, Signal, Slack, Telegram, WhatsApp, WebChat,
with Matrix, Nostr and others as on-demand channel plugins), plus paired mobile
nodes for camera, screen and voice. Tools and skills are a deliberately bounded
set; sandboxing and security are treated as half the product.

**Take:** the gateway is the right shape for human contact. Channels are not an
afterthought bolted onto an agent — they are a declared surface with their own
security posture. Our spec had `ChannelClass` as an enum and little else; that
is not enough.

**Decline:** the personal-assistant framing, consumer messengers (WhatsApp,
Signal, Telegram) as first-class enterprise channels, and paired device nodes.
Enterprise contact happens on Teams, Slack, mail and ticketing.

### Microsoft 365 Copilot / Copilot Studio / Agent 365 — the control plane
The most complete enterprise governance story. Three ideas matter:

1. **A registry as the single source of truth** — every agent in the
   organization inventoried with owner, deployment platform, permissions and
   policy alignment, including agents built elsewhere (Bedrock, Google Cloud)
   synced in for cross-platform visibility.
2. **Agent identity with a lifecycle** — each agent gets its own Entra Agent ID;
   access lifecycle policies exist specifically so agents "don't accumulate
   stale permissions". Connector permissions are attached to that identity and
   visible to admins without leaving the admin centre.
3. **Declarative agents** — an agent is instructions + grounding knowledge +
   actions, declared rather than coded, which is the same bet our spec makes.

**Take:** the registry, the identity lifecycle (including *de-provisioning* and
stale-permission review), grounding knowledge as a declared block, and admin
visibility of "which agent can call what" as a generated artifact.

**Decline:** coupling to one identity provider. Our equivalent must be
provider-neutral and compile *to* Entra, IAM or Cloud Identity.

### Claude Cowork — scheduled tasks and packaged capability
Agentic knowledge work outside coding, with two features worth stealing:
**scheduled tasks** (a packaged prompt run on a cadence — hourly, daily, weekly
— with the same tools, skills and plugins as an interactive task), and
**plugin bundles** packaging skills, connectors and sub-agents together,
distributed through an organization's private marketplace. Enterprise plans add
admin controls, usage analytics and OpenTelemetry observability.

**Take:** scheduling as a first-class object with the *same* capability and
permission envelope as interactive work — not a separate cron system with its
own credentials. We already have the marketplace; we had no scheduling at all.

### Agency Swarm — declared communication flows
Agents are organised by role, and **communication flows are explicitly
directional**: `CEO > Developer` means the CEO may initiate with the Developer
and not the reverse. A shared `agency_manifesto.md` carries principles across
all agents; each agent has its own instructions.

**Take:** directional, *declared* flows. Our org tree implies delegation edges,
which is right for hierarchy but cannot express "the analyst may consult
compliance but not instruct it". A flow block makes lateral interaction
reviewable instead of inferred.

### Swarms / OpenAI Swarm / AgentScope — topology vocabulary
Sequential, concurrent, hierarchical and mesh topologies; handoffs as the
primitive; MCP integration as standard. OpenAI's Swarm is explicitly
educational and superseded by the Agents SDK.

**Take:** the topology vocabulary as a *pattern* on top of the org tree, not a
replacement for it. Handoff is already a runtime requirement in our spec.

**Decline:** mesh-by-default. An organization where every agent can talk to
every agent has no reviewable permission story, which is the whole point of the
org model.

### LangGraph Platform — durable execution
Durable state that survives failure, checkpointing at every step, interruptible
execution a human can inspect and steer, background runs with webhook
completion, and cron scheduling. Enterprise tiers add SSO and RBAC.

**Take:** durability and resumability are a *declared property* of a system, not
an implementation accident. A scheduled overnight run that dies at step 40 and
restarts from zero is worse than no automation.

### Slack / Teams as agent surfaces, and human-in-the-loop practice
The consistent 2026 enterprise pattern is permission systems separating what an
agent may do autonomously from what needs approval, confidence thresholds
triggering human review, and a drafts channel where the bot proposes, a human
approves, and only then does it commit. An "Agent-to-Human" protocol has been
proposed to treat humans as participants in agent systems rather than external
observers.

**Take:** approval is a routing problem, not a boolean. Who approves, on which
channel, within what SLA, and what happens when nobody answers at 3am.

---

## 2. Capability matrix

Legend: ● have it · ◐ partial · ○ missing (before this round)

| Capability | OpenClaw | M365 / Agent 365 | Cowork | Agency Swarm | LangGraph | **Us (before)** |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Declarative agent definition | ◐ | ● | ◐ | ◐ | ○ | ● |
| Org hierarchy / teams / leaders | ○ | ○ | ○ | ◐ | ○ | ● |
| Declared communication flows | ○ | ○ | ○ | ● | ○ | ◐ |
| Roles + responsibilities as contract | ○ | ◐ | ○ | ◐ | ○ | ● |
| RBAC, policy, least privilege | ◐ | ● | ◐ | ○ | ◐ | ● |
| Per-agent identity + lifecycle | ○ | ● | ○ | ○ | ○ | ◐ |
| Agent registry / inventory | ○ | ● | ◐ | ○ | ○ | ◐ |
| Sandboxed execution | ● | ◐ | ● | ○ | ○ | ● |
| MCP / tool binding | ● | ● | ● | ● | ● | ● |
| Human channels (Teams/Slack/mail) | ● | ● | ◐ | ○ | ○ | ◐ |
| Approval routing + escalation + SLA | ◐ | ◐ | ◐ | ○ | ◐ | ◐ |
| **Scheduling / recurring runs** | ◐ | ● | ● | ○ | ● | **○** |
| **Event & webhook triggers** | ● | ● | ◐ | ○ | ● | **○** |
| **Knowledge grounding sources** | ◐ | ● | ● | ◐ | ○ | **○** |
| **Durable execution / resume** | ○ | ◐ | ◐ | ○ | ● | **○** |
| **Lifecycle stages + promotion gates** | ○ | ● | ○ | ○ | ◐ | **○** |
| **Evaluations as a gate** | ○ | ◐ | ○ | ○ | ◐ | **○** |
| **Cost budgets + breach action** | ○ | ◐ | ◐ | ○ | ◐ | **○** |
| **Compliance: residency, retention** | ○ | ● | ◐ | ○ | ○ | **◐** |
| Marketplace / packaged bundles | ◐ | ● | ● | ○ | ○ | ● |
| Observability / tracing | ◐ | ● | ● | ○ | ● | ● |
| **Generates deployable IaC** | ○ | ○ | ○ | ○ | ○ | ● |

The last row is the honest differentiator: none of the comparable systems
*generate* the infrastructure. They are runtimes you configure. We are a
compiler, which is why the bolded gaps hurt more — a runtime can add scheduling
as a feature, but a compiler that cannot express scheduling cannot generate it
for any target.

## 3. What we are adding as a result

| Gap | Decision | Where |
|---|---|---|
| Scheduling and event triggers | ADR-0020 | `spec.triggers`, `scheduling.py` |
| Human channels, routing, escalation, SLA | ADR-0021 | `spec.channels`, `humans.py` |
| Explicit definition vs implementation phases | ADR-0019 | `phases.py` |
| Registry, lifecycle stages, promotion gates, evals | ADR-0022 | `spec.lifecycle`, `REGISTRY.md` |
| Knowledge grounding as declared sources | ADR-0023 | `spec.knowledge` |
| Declared directional interaction flows | ADR-0024 | `spec.interaction_flows` |
| Durable execution and resumability | ADR-0025 | `spec.resilience` |

Cost budgets and compliance (residency, retention, redaction) land with
ADR-0022 as part of the enterprise bundle rather than as separate decisions,
because they are enforced at the same points.

## 4. What we deliberately decline

- **Consumer messaging channels.** Enterprise contact is Teams, Slack, mail and
  ticketing. Adding WhatsApp and Signal widens the compliance surface for a
  population we do not serve.
- **Mesh topology by default.** Every-agent-to-every-agent destroys the
  reviewable permission story that is the point of the org model. Lateral
  contact must be declared.
- **Binding to one identity provider.** Entra Agent ID is the best-executed
  version of per-agent identity; we compile *to* it rather than depend on it.
- **A hosted control plane as the only option.** Agent 365's registry is
  excellent and centralized; ours is a generated artifact the customer owns.
- **Autonomous spend.** No agent gets an unbounded budget; a breach action is
  mandatory, not optional.

---

## 5. Second pass: three agent frameworks, read directly

September 2026. A closer read of the three frameworks our runtime adapters
target, looking specifically for primitives we had not modelled.

### OpenAI Agents SDK — what we were missing
Its primitives are agents, handoffs, **guardrails**, sessions, tracing, tools
and human-in-the-loop, plus sandbox, realtime and voice agents. Two gaps:

* **Guardrails as a first-class, configurable primitive** for input and output
  validation. We had permissions — *what an agent may reach* — and nothing for
  *what may pass*. A correctly-permissioned agent could paste a customer's
  identifiers into a chat channel and no rule in our model said otherwise.
* **Structured output types.** Agents declare the shape they return. Our
  sub-agents declared `returns: "a cited findings list"` — useful to a reader,
  uncheckable by a caller.

**Declined:** realtime and voice agents. A voice modality is a real product
surface and not one an enterprise agentic *designer* needs before it can
deploy text and tool work; adding it would widen the channel model
substantially for a case nobody has asked us for.

### deepagents — what we were missing
Planning, a virtual **filesystem** over pluggable backends, sub-agents with
isolated context, **context management** (summarize long threads, offload tool
outputs to disk), persistent memory, human-in-the-loop, skills and shell
access. Two gaps:

* **Context management.** We had memory (what an agent *learned*) and sandboxes
  (where it *executes*) but nothing for the context window itself. A tool
  returning 200 KB put 200 KB in the window, and a long run simply grew until
  it broke or became expensive.
* **A workspace / artifact store.** The place large intermediate material
  lives. Conflating it with memory is how a scratch file becomes permanent
  knowledge nobody classified.

**Noted, not yet built:** an explicit planning/todo primitive. Our encoded
workflows cover auditable processes; a lightweight in-run plan is a different,
smaller thing and is backlog (WS-024), not a decision.

### Agency Swarm — what we were missing
Directional communication flows (which we adopted in ADR-0024), typed tools
with Pydantic validation, a `ToolFactory` that turns OpenAPI schemas into
tools, thread-persistence callbacks, and **shared instructions** — an
`agency_manifesto.md` every agent carries. One gap:

* **Shared operating instructions.** We had role responsibilities per agent and
  nothing that said "this is how *everyone here* works". Every organization has
  those, and repeating them in each role is how they drift.

**Noted, not yet built:** OpenAPI-derived tools. A capability binding that
generates tools from a schema is genuinely useful and is backlog (WS-024).

## 6. What this pass added

| Gap | Decision | Where |
|---|---|---|
| Guardrails on input and output | ADR-0035 | `guardrails.py`, `spec.guardrails` |
| Context compaction and offloading | ADR-0036 | `context.py`, `spec.context` |
| Artifact store (agent workspace) | ADR-0036 | `spec.artifact_stores` |
| Checkable output contracts | ADR-0037 | `spec.output_contracts` |
| Shared operating instructions | ADR-0038 | `operating_principles`, team `shared_instructions` |

Backlog from this pass, recorded rather than decided: a planning primitive,
OpenAPI-derived tools, and realtime/voice as a channel modality (WS-024).

## 7. Third pass: a component taxonomy, checked against what we built

Source: NVIDIA's agentic-AI overview, supplied by the user as text.
`nvidia.com` and `blogs.nvidia.com` are blocked by this environment's egress
proxy, so this pass is against the text we were given, not a page we read.
The same applies to OpenClaw and NemoClaw, which that text references and
which we have not been able to verify beyond an earlier pass's sources.

Useful precisely because it is somebody else's decomposition. Where it matches
ours, that is corroboration. Where it does not, it is a gap — and the gaps
cluster in one place, which is the finding.

### Components

| Their component | Ours | State |
|---|---|---|
| **LLM / agent core** — reasons, plans, selects tools, within guardrails and policy | Model policy per agent, catalog-approved models, composed prompt | Built |
| **Harness** — connects memory, knows the tools, *can create new skills if allowed* | `harness/`: MCP, SQL policy, skills, plugins, endpoints, sandbox | Built, **except skill creation** |
| **Secure runtime** — per-agent sandbox so a rogue agent cannot reach the host, exfiltrate, or run up cost | Environment classes, per-agent narrowing, provider seam (ADR-0009, ADR-0054) | Built; the local kernel boundary is the known weak point |
| **Memory modules** | Session and long-term tiers, classified namespaces | Built |
| **Planning modules** — CoT/ToT without feedback; ReAct, Reflexion, human-in-the-loop with feedback | Human-in-the-loop approval only | **Gap** |
| **Tools and skills** — skills carry instructions for using a tool; APIs; RAG | Skills, plugins, tools as MCP wrappers, knowledge sources | Built (retrieval not wired) |
| **Systems of models** — open and frontier models together for accuracy, cost and data control | `ModelPolicy` with classes and `subagent_classes` | Partial — **no per-step routing** |

### Agent types

| Their type | Ours |
|---|---|
| Simple reflex | Triggers and cadences (WS-012) |
| Model-based reflex | Session memory and context |
| Goal-based | **Missions** — objective, deliverables, an end date (ADR-0039) |
| Hierarchical | **The org tree** — recursive teams, one leader each (ADR-0006). Our strongest area |
| Multi-agent systems | Delegation, sub-agents as tools, mission peers |
| Learning | **Gap** — memory stores, it does not learn |
| Utility-based | **Gap** — no utility or reward anywhere in the decision path |

### What this pass actually found

The gaps are not scattered. Everything we have built is **structural and
governing**: who reports to whom, what may be reached, what is recorded, what
is refused. Everything missing is **cognitive**: how an agent decides what to
do next, whether it critiques its own output, whether it gets better, and
whether it trades cost against value at decision time rather than at approval
time.

That is a coherent bias, not an accident — the platform is a designer and a
control plane, and the reasoning loop is delegated to an adapter. But it has a
consequence worth stating: an organization designed here is governed well and
*thinks* exactly as well as whichever framework executes it. We have no opinion
about planning, and no way to express one.

Five items follow from this, added to the roadmap rather than decided here:

1. **A planning primitive** with and without feedback — decomposition,
   ReAct-style tool loops, and a reflection step that can revise its own
   output. Today an output contract can refuse an answer and retry it
   (ADR-0037); nothing lets an agent *critique* one.
2. **Learning from outcomes.** Evaluation cases are declared and never run
   (WS-014 M3), so no feedback reaches anything. Without that, "learning agent"
   is not a type we can build.
3. **Utility at decision time.** Cost ceilings apply when a model is approved,
   not when an agent chooses between two courses of action.
4. **Skill creation, governed.** "Can create new skills, if allowed" is the
   interesting half of the harness definition, and the governance question —
   who approves a capability an agent invented — is exactly the kind this
   platform exists to answer. We have no answer.
5. **Per-step model routing.** A routine step on a cheap model and a hard one
   on a frontier model is what "systems of models" means; our policy governs
   which models an agent *may* use, never which it uses *when*.

Their framing of guardrails matches ours and is worth quoting as
corroboration: policy enforcement belongs "at the infrastructure layer—not just
inside the agent code". That is ADR-0050 and ADR-0054's position, and it is why
OpenShell's L7 policy engine is interesting to us.

## 8. OpenClaw, read directly

[OpenClaw](https://github.com/openclaw/openclaw) — MIT, TypeScript/Node,
stewarded by an independent foundation, ~390k stars. A **local-first personal
assistant**: a **Gateway** acting as the local control plane for sessions,
tools, events and channel connections; CLI, TUI and control UI on top of it;
companion apps; pluggable model providers; and a channel layer spanning
WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage and twenty
more. *"State, memory, and credentials live on your hardware."*

It is not a competitor to this platform — it is a personal assistant, not an
enterprise designer — but three things in it are directly useful.

**1. It is the channel layer we have not built.** WS-013 M4 has been open since
the human-channels work landed: our `ChannelClass` is abstract, Slack and Teams
appear only in the binding, and there is no real bridge client behind either —
routing plans are computed against an in-process stub. OpenClaw already
maintains bridges to twenty-plus platforms under MIT. Binding our channel class
to an OpenClaw gateway is a more honest answer than writing and maintaining our
own Slack and Teams clients, and it fits the binding layer exactly as a runtime
adapter or a model provider does. It is now the leading candidate for WS-013
M4, subject to an ADR when somebody implements it. The cost is real and should
be stated in that ADR: a Node daemon becomes a deployment dependency of a
Python platform, and a personal-assistant gateway would be doing an enterprise
messaging job.

**2. Its inbound posture is ours, arrived at independently.** It treats inbound
messages as untrusted input, and DM-capable channels require pairing approval
before anyone can talk to the agent. That is ADR-0035's input boundary and a
control we do **not** have: our approval routing governs what an agent does,
not who may address it in the first place. An unpaired human messaging an agent
is an authorization question we have never asked. Added to the roadmap.

**3. Gateway-as-control-plane keeps recurring.** OpenClaw has one, OpenShell has
one, Agent 365 is one, and our fabric is one. Four independent designs reaching
the same shape is the strongest corroboration this document contains for
ADR-0049's split.

It also marks a deployment posture we do not serve: everything on one person's
hardware, no server, credentials never leaving the device. Our three planes
assume a fabric somebody operates. A single-user local tenant is a coherent
thing to want, and nothing in the architecture forbids it — but nothing
supports it either, and pretending otherwise would be easy.

## 9. Microsoft Agent Framework, read directly

[microsoft/agent-framework](https://github.com/microsoft/agent-framework) at
`d91e44e` — MIT, Python and .NET (Go in a sibling repo), the merged successor
to Semantic Kernel and AutoGen. Read from the source tree, not the marketing.

The short answer to "how do we compare": **we are not the same kind of thing,
and the comparison that matters is not feature-for-feature.** MAF is a runtime
SDK — how one agent runs, how several are orchestrated, how a tool call is
intercepted. This platform is a design and compile layer — who may decide what,
who may reach what, and what is refused before anything runs. MAF's own
`declarative-agents/` tree is the closest it comes to our territory, and it
stops well short of it.

### What it has that we do not

**1. An information-flow control model.** `python/packages/core/agent_framework/security.py`
is 4,511 lines of prompt-injection defence built on labels rather than rules:
`IntegrityLabel` (trusted / untrusted), `ConfidentialityLabel` (public <
private < user_identity), labels combined along a dataflow, and
`check_confidentiality_allowed` refusing at the exfiltration boundary.
Untrusted content never reaches the model as text — it is replaced by a
`var_<id>` handle in a `ContentVariableStore`, and a quarantined LLM inspects
it. MCP tool results are labelled from the server's own annotations.

This is a genuinely different axis from ours and we have nothing like it. Our
`DataClass` plus permissions answers *may this agent read this class*; it does
not track what happened to a value after it was read, so an agent that may read
PII and may call an external endpoint can carry one into the other and no rule
we have notices. That is a real hole, and labels are the known answer to it.
Recorded as a gap, not a plan.

**2. A well-specified fail-closed seam.** `MiddlewareFailure`
(`_middleware.py:86`) is the framework's explicit fail-closed escape, and its
docstring is unusually honest about why one is needed: ordinary exceptions from
function middleware "are converted into tool-error results by the
function-invocation loop, which then keeps running — appropriate for
recoverable tool failures, but **fail-open for enforcement layers and
guardrails**." It cancels the concurrent batch, starts no further call, and
must not be caught. Three seams — agent, chat, function — with matching
contexts.

**3. An approval protocol that survives a remote agent.** ADR-0006 in their
tree works through why a callback does not: the agent must suspend, a network
response must reach the client, and the run must resume. The result is approval
*content types* on the wire plus session-persisted state, with anti-TOCTOU
machinery — if middleware mutates arguments after approval, a replacement
approval is forced (`_tools.py:944`). We have the authority model for approvals
and a weaker story about the round trip.

**4. Micro-VM sandboxing that ships.** Hyperlight gives per-run snapshot and
restore, file mounts and an outbound domain allow-list, with `LocalCodeAct` as
the un-isolated sibling that says so. ADR-0054 chose a pluggable sandbox
preferring a kernel boundary; Hyperlight is a concrete provider for it.

### What we have that it does not

**No organizational model at all.** This is the headline, and I checked rather
than assumed: there is no role, no permission, no capability grant attached to
an agent, no mandate, no separation of duties, no autonomy posture, and no
person. Searching the core package for a role or permission type returns file
permissions and a `PermissionError` retry on Windows.

What looks like identity is not:

| Their thing | What it actually is |
|---|---|
| `_Principal(tenant_id, user_id)` (`security.py:113`) | A **data label**, unioned along a dataflow and subset-checked at exfiltration. Not an actor. |
| `AgentIsolationKeyProvider` (.NET hosting) | A **storage partition key** off an OIDC claim, with a warning that a non-unique claim collides two principals. Not an authorization subject. |
| `OnBehalfOf` (`LoopAgentOptions`) | Message **relabelling**. Not an OAuth OBO flow; there is none. |
| `ApprovalMode` (`_tools.py:215`) | `always_require` \| `never_require`, defaulting to never. Says *whether*, never *who*. |

Set that last row against ADR-0072's four postures and ADR-0079's checked
approver, and the difference is the whole point of this platform: MAF can ask a
human; it cannot say which human, whether that human holds the decision, or
whether they are the same person who owns the agent that raised it.

**Their declarative layer cannot describe an organization.** `kind: Prompt` is
strictly one agent — name, instructions, model, tools, schemas. `kind: Workflow`
is the multi-agent dialect, and it is Copilot Studio's bot object model with
Power Fx expressions: an imperative action list (`InvokeAzureAgent`,
`ConditionGroup`, `GotoAction`, `SetVariable`) with an `agents:` map. Their
`CustomerSupport.yaml` expresses a five-agent escalation as nested branches on
a string variable. And the first-class topologies — handoff, group chat,
magentic, concurrent — have **zero** declarative surface; grep the declarative
packages for `HandoffBuilder` and you get nothing. So nothing about a
multi-agent organization is reviewable as a spec artifact, which is the
artifact this platform exists to produce.

There is also no published schema for either dialect. The Python grammar is the
class hierarchy in `_models.py`; the .NET grammar lives in an out-of-repo NuGet
(`Microsoft.Agents.ObjectModel`). Two implementations, no shared contract.

**Two fail-open behaviours to know about.** An unknown action `kind` in a
declarative workflow is logged and **skipped**, not rejected
(`_declarative_builder.py:478`; their own tests assert the log line) — so a
workflow authored against a newer action set silently loses steps. And the
function-middleware default described above converts an enforcement error into
a tool result unless the author knew to raise `MiddlewareFailure`.

### Are we compatible?

**At the design layer, no, and that is the right answer.** Their declarative
format is a sibling of ours in syntax and a different thing in purpose: it
describes how work flows, ours describes who may do it. Neither can be
generated from the other without inventing the half it does not have. Adopting
theirs would mean giving up the org model; adopting ours would mean giving up
Power Fx and the Foundry action catalogue. They should stay separate.

**At the implementation layer, yes — it is a clean fit for a supported
binding.** ADR-0067 makes deep agents the reference runtime and keeps the spec
free of framework names, and MAF slots into that seam exactly as the OpenAI
Agents SDK does. `FunctionMiddleware` + `MiddlewareFailure` is a better
documented version of the boundary our `HarnessBuilder.guarded()` already
binds, `ContextProvider` is where a filesystem or sandbox policy attaches, and
`AgentSkillsProvider` maps onto our skills.

The place the two agree most is the one that matters. ADR-0067 rule 5 says
governance never delegates to framework middleware as its only enforcement.
MAF's own agent-hooks record
(`docs/decisions/0035-dotnet-agent-hooks-enforcement.md`) closes with: *"The
trust model is the spec's: cooperative contract, not a security boundary."*
Two independent designs, same conclusion — the framework seam is defence in
depth, and the gate is somewhere else.

**Take:** a MAF binding is worth building as a *supported binding* (ADR-0067
rule 3 obligations: build against the installed SDK, assert structurally, no
claim of parity). It is the most likely runtime in a Microsoft shop, and
refusing to bind to it would cost us those organizations for no design reason.

**Decline:** MAF as the reference runtime, its declarative format as an input
or output of our compiler, and any arrangement where a middleware verdict is
the only thing standing between an agent and a tool.

**Take, separately and more urgently:** information-flow labels. That gap is
ours whatever runtime we bind to.

---

## Sources

- Fourth pass, read directly: [microsoft/agent-framework](https://github.com/microsoft/agent-framework) at `d91e44e` — `python/packages/core/agent_framework/{security.py,_middleware.py,_tools.py,_harness/}`, `python/packages/declarative/`, `python/packages/orchestrations/`, `declarative-agents/`, `dotnet/src/Microsoft.Agents.AI.{Harness,Hyperlight,Declarative,Hosting}`, and their `docs/decisions/` (0006 user approvals, 0031 per-user session isolation, 0035 agent-hooks enforcement)
- Third pass, read directly: [OpenClaw](https://github.com/openclaw/openclaw) · [NVIDIA OpenShell](https://github.com/NVIDIA/openshell) · [Docker Sandboxes](https://www.docker.com/products/docker-sandboxes/)
- [OpenClaw documentation](https://docs.openclaw.ai/) · [OpenClaw overview](https://openclaw.ai/) · [Milvus: complete guide to OpenClaw](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md) · [Yowox: the self-hosted AI gateway](https://yowox.com/posts/openclaw-guide-ai-gateway/)
- [Microsoft Agent 365: the control plane for AI agents](https://www.microsoft.com/en-us/microsoft-365/blog/2025/11/18/microsoft-agent-365-the-control-plane-for-ai-agents/) · [Agent 365 GA announcement](https://www.microsoft.com/en-us/security/blog/2026/05/01/microsoft-agent-365-now-generally-available-expands-capabilities-and-integrations/) · [Governing agent identities — Entra ID Governance](https://learn.microsoft.com/en-us/entra/id-governance/agent-id-governance-overview) · [Manage Entra Agent IDs in Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/admin-use-entra-agent-identities) · [Copilot Studio security and governance](https://learn.microsoft.com/en-us/microsoft-copilot-studio/security-and-governance)
- [Claude Cowork](https://claude.com/product/cowork) · [Schedule recurring tasks in Cowork](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork) · [Cowork enterprise administrator guide](https://claude.com/resources/tutorials/claude-cowork-enterprise-administrator-guide) · [Building agents with Claude: skills to scheduled tasks](https://hatchworks.com/blog/claude/building-agents-with-claude/)
- [Agency Swarm](https://github.com/VRSEN/agency-swarm) · [Swarms framework](https://github.com/kyegomez/swarms) · [OpenAI Swarm](https://github.com/openai/swarm) · [awesome-agent-swarm](https://github.com/EvoMap/awesome-agent-swarm)
- [LangGraph](https://github.com/langchain-ai/langgraph) · [The runtime behind production deep agents](https://www.langchain.com/blog/runtime-behind-production-deep-agents) · [Durable execution in LangGraph](https://vadim.blog/durable-execution-agents-that-survive-failure-and-resume-where-they-left-off)
- Second pass: [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) · [deepagents](https://github.com/langchain-ai/deepagents) · [Agency Swarm](https://github.com/VRSEN/agency-swarm)
- [Slack: best agentic AI platforms 2026](https://slack.com/blog/productivity/best-agentic-ai-platforms-for-2026-what-they-are-and-how-to-choose-one) · [A2H: Agent-to-Human protocol](https://arxiv.org/html/2602.15831v1) · [AI agents in 2026](https://symphony-solutions.com/insights/ai-agents-in-2026)
