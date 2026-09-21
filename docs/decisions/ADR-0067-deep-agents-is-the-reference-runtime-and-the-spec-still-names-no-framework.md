---
id: ADR-0067
title: Deep agents is the reference runtime and the spec still names no framework
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture]
consulted: [Runtime Engineering, Product]
informed: [All engineering]
scope: [runtime, compiler, security]
workstreams: [WS-008]
supersedes: []
superseded_by: []
related: [ADR-0002, ADR-0027, ADR-0035, ADR-0036, ADR-0040, ADR-0054]
tags: [frameworks, runtime, strategy]
---

# ADR-0067: Deep agents is the reference runtime and the spec still names no framework

## Context
Four runtime adapters exist — LangChain deep agents, the OpenAI Agents SDK,
native LangGraph and a dependency-free echo. Until this point they were
nominally equal, which in practice meant none of them worked: all of them were
thin lazily-imported bindings, and every test ran on `echo`. Installing two of
them found four mismatches in an afternoon, including a deep agents call
signature two major versions stale that would have raised on first contact.

Equal treatment is not sustainable. The integrations that make an agent
*governed* rather than merely *running* — filesystem permission, sandboxed
execution, MCP mounting, context policy, the evaluation gate — each have to be
built against a concrete framework's primitives. Building them four times is
not a plan, and building them zero times is the status quo.

Both candidates are stronger than a summary of either suggests, and each is
strong in a different place.

**The OpenAI Agents SDK** carries `InputGuardrail`, `ToolInputGuardrail`,
`ToolOutputGuardrail` and `OutputGuardrail` — the four boundaries ADR-0035
declares, matched one for one, which no LangChain construct does. It has
native MCP with a per-tool approval hook, `Session` for conversation state,
built-in tracing, `Agent.as_tool` matching ADR-0027's bounded call, and a
`max_turns` that means turns. Its API is small and stable.

**LangChain deep agents** carries what the SDK does not: `FilesystemPermission`
(`operations`, `paths`, `allow`/`deny`/`interrupt`) over a virtual filesystem,
an execution-policy seam (`DockerExecutionPolicy` and siblings behind
`ShellToolMiddleware`), context editing and summarization, memory, skills,
rubric grading, and sub-agents with explicit `isolated`/`fork` modes. Its model
initialisation is provider-neutral across exactly the five providers
`ModelSpec` enumerates.

The deciding difference is not feature count. The OpenAI SDK's best parts —
`HostedMCPTool`, `CodeInterpreterTool`, `ComputerTool`, `ApplyPatchTool` — are
**one vendor's platform features rather than library features**. Depending on
them binds a customer's deployment to that platform, not merely to that
library. A platform dependency and a library dependency fail differently and
are escaped at very different cost.

## Decision
**LangChain deep agents is the reference runtime. The OpenAI Agents SDK
remains a supported binding. The spec continues to name no framework.**

1. **The spec names no framework** (ADR-0002, unchanged). `Runtime` is a
   binding concern chosen per agent, and nothing in this record puts a
   framework name into a system spec, the IR, or any target.
2. **Reference runtime means four obligations.** Deep agents must execute in
   CI against the real framework; it receives the deep integrations first
   (filesystem governance, sandbox execution, MCP, context policy); the
   designer's dry-run and the evaluation gate run against it; and a change
   that breaks it blocks a release.
3. **A supported binding means two.** It must build against its installed SDK
   and be asserted structurally — that sub-agents are tools and not handoffs,
   that limits translate correctly. It is not required to carry the deep
   integrations, and saying so plainly is better than implying parity.
4. **Hosted platform tools are not used at the reference layer.** Where a
   binding uses them, it is declared, and the sandbox boundary statement
   (ADR-0054) records that the boundary belongs to a third party.
5. **Governance never delegates to framework middleware as its only
   enforcement.** Permission, delegation legality, mandate (ADR-0065) and
   approval decisions stay in the tools and at the adapter seam, because a
   check inside a middleware holds for one framework and means nothing to the
   next. Framework middleware may be added on top — defence in depth — and may
   never be the sole check.
6. **Framework versions are pinned to the API the adapter targets**, and every
   adapter is exercised against its real framework with a scripted model. The
   deep agents signature went stale across two major versions without a single
   test failing; a pin without execution catches nothing.
7. **This is revisited** if the reference framework's churn costs more than
   the integrations it saves, if provider neutrality stops being a
   requirement, or if a customer commitment makes a platform dependency
   acceptable. Those are the conditions; anything else is not a reason to
   re-open it.

## Scope
Which adapter is exercised, integrated against and demoed, and where
governance checks may live. It does not change the spec, the IR, any generated
target, the permission model, or the availability of the other adapters.

## Implementation
Deep agents gains the integrations it already has primitives for:
`ModelCallLimitMiddleware(run_limit=max_turns)` in place of the `recursion_limit`
arithmetic, `FilesystemPermission` derived from the agent's data grants so its
virtual filesystem stops bypassing the artifact store, and a reconciliation of
`harness/mcp.py` with `langchain-mcp-adapters`. The OpenAI adapter keeps its
build assertions and gains nothing further. `Runtime.LANGGRAPH` stays as a
thin binding for workflow-shaped agents.

## Timeline
Phase 5, WS-008, ahead of the alpha. ALPHA A5 (a live model answering) is
scoped to this runtime.

## Advantages
- One framework gets deep enough integration to be genuinely governed, instead
  of four getting none.
- Provider neutrality is preserved where it is structural: ADR-0040 resolves a
  model class to any of five providers, and the reference runtime can reach all
  of them.
- One dependency family serves two seams, since LangGraph already underpins the
  workflow engines.
- The primitives we would otherwise have to invent — filesystem permission,
  execution policy, context editing — already exist and are tested.
- Saying which runtime is real stops the honesty table from carrying four rows
  that all mean "contract".

## Disadvantages
- **We are paying a churn tax deliberately.** deepagents moved 0.0.5 → 0.7.15
  and broke our adapter silently; its profiles API is beta-flagged. The
  stabler SDK is the one we did not choose, and this cost recurs.
- **We pass up an exact match.** The OpenAI SDK's four guardrail boundaries
  line up with ADR-0035 one for one. Nothing in LangChain does, so our
  guardrails stay our own implementation on the reference path.
- **Framework-shaped thinking will leak.** Once one runtime is real, its
  vocabulary starts arriving in design conversations, and the spec's neutrality
  is then defended by rule 1 and by whoever is reviewing — not by anything
  structural.
- **Two mechanisms for one concern invites the wrong one to win.** There are
  now two HITL paths, two limit paths, two boundary-screening paths. Rule 5
  says which is authoritative; nothing enforces rule 5 except review.
- **"Supported binding" will be read as "works".** It means it builds and is
  structurally asserted. A customer choosing it will find the integrations
  thinner, and the honesty table has to keep saying so.
- **The bet is that provider neutrality matters.** If deployments turn out to
  be single-vendor in practice, we chose the larger, less stable dependency for
  a property nobody used.

## Alternatives considered
- **The OpenAI Agents SDK as reference.** Smaller, stabler, and its guardrails
  match ours exactly. Rejected because its strongest tools are platform
  features: a deployment built on them is bound to a vendor's platform, which
  is a far more expensive dependency to escape than a library.
- **Keep all adapters equal.** What we had. It reads as flexibility and
  delivers four contracts and zero runtimes.
- **Build our own agent loop.** Maximum control, no framework churn, and it
  means maintaining tool calling, retries, streaming and provider quirks
  ourselves — work that is not the product.
- **Defer until a customer decides.** Defensible, and it leaves the alpha with
  no runtime that can think, which is what ALPHA A5 already says is
  unacceptable.

## Verification
Tests assert: the deep agents adapter executes against the real framework in
CI; the OpenAI adapter builds against its installed SDK with sub-agents as
tools and no handoffs; a harness limit expressed once resolves correctly on
both; a permission, mandate or approval decision is refused by the tool layer
even when the framework's own middleware would have allowed it; and no spec,
IR or generated target contains a framework name.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Deep agents is the reference runtime, the OpenAI Agents SDK is a supported binding, hosted platform tools are excluded from the reference layer, and governance never delegates its only check to framework middleware. |
