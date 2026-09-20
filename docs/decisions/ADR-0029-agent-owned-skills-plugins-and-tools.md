---
id: ADR-0029
title: Skills, plugins and tools are held per agent, and tools only wrap
status: Accepted
version: 1.0.0
date: 2026-09-20
updated: 2026-09-20
deciders: [Platform Architecture]
consulted: [Security Engineering, Developer Experience]
informed: [All engineering]
scope: [spec, compiler, runtime]
workstreams: [WS-019]
supersedes: []
superseded_by: []
related: [ADR-0010, ADR-0007, ADR-0027, ADR-0030]
tags: [capability, integration]
---

# ADR-0029: Skills, plugins and tools are held per agent, and tools only wrap

## Context
The runtime had skills and plugins; the spec did not. So the thing that decides
*how* an agent works was invisible in the artifact we review, sign and compile.
Meanwhile "tool" was used loosely for three different things: an MCP tool, a
capability, and a convenience wrapper someone wrote.

The distinction matters for review. A capability is **access** — it is what an
agent may reach, and granting one is a security decision. A skill is
**instruction** — it changes how the agent works and grants nothing. A tool is
a **name** — a narrowed, documented handle on something already granted. Muddle
them and a reviewer cannot tell which lines of a spec widen the blast radius.

## Decision
Three distinct spec objects, each held per agent:

* **Skill** — instructions plus optional resources, with the capabilities it
  assumes declared so a skill held without its access is flagged. Grants
  nothing.
* **Plugin** — a bundle providing skills and tools, declaring the capabilities
  it requires. Installing a plugin whose requirements the agent lacks is an
  error, not a runtime surprise.
* **Tool** — a named wrapper over exactly one thing the agent **already
  holds**: a capability, a sub-agent, a workflow or an external endpoint. A
  tool may narrow (fewer rows, one operation, an added approval); it may never
  widen, and it inherits its target's approval requirement.

The rule that makes this reviewable: **a tool grants nothing**. If a spec line
increases what an agent can reach, it is a capability or an endpoint, and it is
reviewed as one.

## Scope
Per-agent capability packaging in the spec, the IR, the registry and the
runtime harness. Access itself remains capabilities bound to MCP servers
(ADR-0010); marketplace distribution remains the catalog.

## Implementation
`spec.model.SkillSpec`, `PluginSpec`, `ToolSpec` and the agent's `skills`,
`plugins`, `tools` lists. `compiler.ir` resolves the effective set — direct
plus plugin-provided — and folds skill instructions into the composed system
prompt with provenance. Approval inheritance is computed in the IR, so the
wrapper's gate is visible in the registry. The validator rejects a tool
wrapping something the holder lacks, a plugin whose requirements are unmet, and
unknown references in either direction.

## Timeline
Phase 2, alongside sub-agents, which tools may also wrap.

## Advantages
- A reviewer can tell at a glance which lines widen access and which do not.
- Skills are versioned, reusable and visible in the artifact under review.
- Plugins fail at validation when their requirements are unmet, not at runtime.
- Wrappers give agents small, well-named tools without new permissions.

## Disadvantages
- Three concepts where teams previously had one loose one; the boundary between
  a skill and an instruction in a role will be argued about.
- Skills are prose, so nothing verifies that an agent follows one.
- Plugin-provided tools appear on agents that never declared them, which is
  convenient and makes the effective toolset less obvious in the spec.
- Wrapper constraints layer on top of the target's, and reasoning about the
  combined limits takes care.

## Alternatives considered
- **Leave skills and plugins in the runtime** — keeps the spec smaller and
  makes half of an agent's behaviour invisible to review.
- **Treat every tool as a capability** — uniform, and it turns harmless
  renaming into a security review.
- **Plugins as the only packaging** — forces a bundle around every single
  skill.

## Verification
Tests assert plugin-provided skills and tools reach the agent, that a wrapper
inherits its target's approval and appears in the agent's gated list, and that
a tool wrapping an unheld capability is rejected by the validator.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-20 | Accepted. Skills instruct, plugins bundle, tools only wrap. |
