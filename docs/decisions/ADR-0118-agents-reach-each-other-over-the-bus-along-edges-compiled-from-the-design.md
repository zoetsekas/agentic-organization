---
id: ADR-0118
title: Agents reach each other over the bus along edges compiled from the design, and the sender, the broker and the receiver each refuse the rest
status: Accepted
version: 1.1.0
date: 2026-09-23
updated: 2026-09-24
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime, Operations]
informed: [All engineering]
scope: [compiler, targets, runtime, security]
workstreams: [WS-006, WS-013, WS-019]
supersedes: []
superseded_by: []
related: [ADR-0006, ADR-0024, ADR-0027, ADR-0039, ADR-0058, ADR-0059, ADR-0065, ADR-0070, ADR-0081, ADR-0093, ADR-0109, ADR-0112, ADR-0114, ADR-0116]
tags: [runtime, bus, nats, delegation, security, separation-of-duties, tracing]
---

# ADR-0118: Agents reach each other over the bus along edges compiled from the design, and the sender, the broker and the receiver each refuse the rest

## Context
The local target runs one container per agent (ADR-0109) and has generated a
NATS service for them since ADR-0059, and handed every worker
`ORGAGENTS_BUS_URL` — and nothing used it. An agent could not message or
delegate to another: in-process `delegate` would have run the other agent
*inside the caller's container*, under the caller's network and credentials,
which ADR-0109 exists to prevent. The organisation the design describes stopped
at each container's edge.

Three things constrain how to close that:

* **The wire is not the boundary** (ADR-0058, ADR-0059 rule 4). Being able to
  publish to a subject is not permission to reach an agent; the receiver must
  decide on its own account.
* **Reach must come from the design.** ADR-0024 says undeclared lateral contact
  is not permitted; ADR-0081 says a unit link grants nothing it does not name;
  ADR-0039 says a mission's reach ends with the mission. A worker that worked
  out its reach at run time from the whole spec would be a second, divergent
  implementation of the org chart, and one the broker could not share.
* **Separation of duties must survive a hop** (ADR-0070). A buyer that cannot
  pay an invoice must not be able to have payables pay it by asking.

## Decision
**Each worker is on the tenant's NATS as its own broker user, with three tools
— `send_message(to_agent, text)`, `delegate(to_agent, task, inputs)` →
handle, `check_delegation(handle, wait_s)` — and an inbox it consumes. Who may
reach whom is computed at compile time, written into the agent's own manifest
and into the broker's configuration, and enforced three times.**

1. **Edges are compiled, from concepts the design already has.** No new spec
   concept is introduced (ADR-0112). `orgagents.compiler.links` reads the IR:

   | Source in the design | Edge |
   |---|---|
   | a team's leader (ADR-0006) | **delegate** to each member and each sub-team leader |
   | `delegates_to` peers and shared services | **delegate** |
   | `interaction_flows` kind `delegate` (ADR-0024) | **delegate** |
   | a mission with internal delegation (ADR-0039) | **delegate**, between the mission's dates only |
   | `interaction_flows` kinds `consult`, `notify`, `escalate` | **message** |
   | the reporting line (`reports_to`) | **message** — the tree's own escalation path, never delegate |
   | unit link `oversees` / `escalates_to` (ADR-0081) | **message**, source unit's leader → target unit's leader |
   | unit link `serves` | **message**, the served unit's leader → the serving unit's leader |
   | unit link `partners_with` | nothing — declared inert |

   A delegate edge permits a message. A reply travels back on the edge it
   answers and needs none of its own. Anything not in the table is not an
   edge.

2. **Each agent gets only its own edges.** `agents/<id>.json` gains `links`:
   outbound and inbound edges (with a mission's window), its subjects, stream,
   consumer and broker user, its `max_delegation_depth`, the separations and
   *which agents hold a separated decision* — not the design. The worker
   never loads another agent's reach.

3. **The broker enforces the same edges.** The local target renders
   `nats/nats.conf` from the same function: one user per agent whose publish
   permission is exactly `…agent.<to>.inbox.<itself>` for each outbound edge
   and `…agent.<from>.reply.<itself>` for each inbound one, plus pulling and
   acking its own durable consumer; its only subscribe permission is its own
   reply-inbox prefix `_INBOX_<agent>.>`. **Every broker identity is an NKey**
   (v1.1): the user entry is `nkey: $ORGAGENTS_BUS_NKEY_<AGENT>`, a reference
   to the agent's *public* key in the broker's environment; the client proves
   it holds the seed by signing the server's nonce. `local_stack.py` generates
   one NKey per agent and one for the operator into the git-ignored `.env`
   (`ORGAGENTS_BUS_SEED_<AGENT>` / `ORGAGENTS_BUS_NKEY_<AGENT>`); the broker is
   given only public keys, each worker only its own seed
   (`ORGAGENTS_BUS_NKEY_SEED`), `bus-init` only the operator's. Anything that
   reads the broker's configuration or environment can recognise every agent
   and impersonate none. A one-shot `bus-init` service, as a bus operator no
   agent can act as, creates the tenant stream (`ORGAGENTS_<TENANT>` on
   `orgagents.<tenant>.agent.>`) and one durable pull consumer per agent;
   workers start after it has succeeded.

4. **The sender is the subject, not the body.** The last subject token names
   the sender and the broker lets a user publish only with its own name there.
   A receiver reads the sender from the subject and refuses a body claiming
   anyone else.

5. **Three refusals.** The sending worker refuses an edge its links do not
   have, a delegation past its depth, and a delegation whose declared
   decision a separation forbids to the chain — a tool error the agent sees,
   and an audit event. The broker refuses a publish outside the user's
   permissions. The receiving worker refuses, on its own account, a sender or
   kind its inbound edges do not have, an out-of-window mission, a spoofed
   sender, and a separated decision; a refused delegation is answered on the
   sender's reply subject so its handle settles.

6. **Separation of duties holds across the chain.** Every hop carries the
   chain of principals that commissioned it. A delegation naming a decision
   is refused, by sender and receiver, when anyone in the chain holds a
   decision the same separation names. *Naming* is `inputs.decision` or
   (v1.1) any separated decision id written in the delegated task text or
   inputs (`pay_invoice`, `pay invoice`, `pay-invoice`, any case). And when
   the receiver runs the task, any tool that constitutes such a decision is
   replaced by a refusal for that run — so the buyer asking the COO to have
   payables pay is refused whether or not the request names `pay_invoice`.

6a. **Each hop is signed; the chain is verified end to end** (v1.1). The
   sending worker signs a hop record — `{v, kind, task (the message id),
   trace_id, from, to, depth, prev, digest, decisions, iat, exp}` — with its
   NKey seed (Ed25519, the key only its worker holds; the same signature
   scheme ADR-0114 uses for approvals, via `cryptography`). `prev` is the
   SHA-256 of the previous signed hop, so the hops form a hash chain from the
   origin; `digest` is the SHA-256 of the kind, text and inputs; `exp` is the
   handle deadline. The receiver, before any separation check, verifies every
   hop against the public keys it was given (`ORGAGENTS_BUS_PUBLIC_KEYS`,
   `agent:U…`): each signature, the hash links, that each hop starts where
   the previous one ended and was made before it expired, that the last hop
   is exactly *this* message from the subject's sender to itself and not
   expired, that the text and inputs are the signed ones, and that the task
   id has not been delivered before (replay). The principals it judges
   separation by, records in its trace and hands to its own run are the
   verified chain's — a `chain` list in the body is not read. A worker on the
   NATS bus without a seed, or whose seed is not the public key the stack
   lists for it, refuses to start.

7. **Delegation returns a handle** (ADR-0093). `delegate` returns at once;
   the receiver runs the task on its own thread and answers on the sender's
   reply subject; `check_delegation` reports `pending`, `completed`, `failed`
   or `refused`, and can block for up to `wait_s`. A handle not answered
   within its deadline settles as failed (rule 5 of ADR-0093).

8. **One trace per chain.** A run started from outside is the root of its
   trace (its session id is the trace id, returned by `/run`); every hop
   carries the trace id, the chain and the depth. Each worker records what it
   sent, received, refused and answered in its audit log and in memory, served
   at `GET /bus/trace/<id>`; the AYC chat stitches the workers' parts into
   the conversation.

JetStream carries every message, so a message to an agent whose container is
restarting waits in its consumer rather than being lost; publish uses message
ids, so a retried publish is not delivered twice.

## Scope
The local target, the worker (`orgagents worker`), the runtime's tool assembly
(a `tool_hooks` seam) and the AYC example. The cloud targets do not yet emit a
broker; they are unchanged. The in-process bus (single-process mode, tests)
is unchanged and remains the default outside the generated stack. External
agents (A2A, ADR-0058) are a different path and are not affected.

## Implementation
- `src/orgagents/compiler/links.py` — `compute_edges`, `agent_links`,
  `nats_permissions`, `nats_config`.
- `src/orgagents/runtime/agent_bus.py` — `LinkPolicy` (the checks),
  `AgentMessenger` (tools, handles, inbox), `separation_guard`,
  `NatsTransport` (nats-py on its own event loop, NKey auth), `bus_init`;
  v1.1: `HopKeys` (sign, `verify_chain`), `named_decisions`,
  `hop_keys_from_env`.
- `src/orgagents/security/nkey.py` (v1.1) — NKey encode/decode, generation,
  sign/verify on `cryptography`, and a shim registered as `nkeys` so
  `nats-py`'s `nkeys_seed_str=` works without the `nkeys` package (an sdist
  with a C dependency). No new dependency.
- `examples/ayc/local_stack.py` (v1.1) — `ensure_bus_nkeys`: seeds and public
  keys into `.env`, `ORGAGENTS_BUS_PUBLIC_KEYS`; v1.0's broker passwords are
  retired (deleted from `.env`) on the next `env`/`up`.
- `src/orgagents/runtime/worker.py` — `load_links`, `attach_bus`,
  `/bus/events`, `/bus/trace/<id>`, `trace_id` on `/run`.
- `src/orgagents/runtime/engine.py` — `AgentRuntime.tool_hooks`, applied
  before the policy wrapper, so a hook can add, replace or remove a tool but
  never get round `guarded`.
- `src/orgagents/compiler/targets/local.py` — `nats/nats.conf`, the
  `bus-init` service, per-agent bus env, `links` in each manifest,
  `orgagents[bus]` in the image; `ORGAGENTS_BUS` now defaults to `nats` there.
- `orgagents bus-init` CLI; `nats-py>=2.7,<3` as the `bus` extra.
- The stub model gains `then call …` turns and `"$last.<key>"`, so a script
  can delegate and then collect; a received task's `inputs.script` lines are
  appended to its prompt, which is how a scripted demo tells the next agent
  what to do.

## Timeline
Delivered with this ADR.

## Advantages
- The organisation reaches across containers, and only where the design says.
- One function decides the edges for the worker and the broker, so they cannot
  drift; a design change is a recompile.
- No single mistake opens an edge: a worker bug, a broker misconfiguration or a
  hand-crafted publish each meets another refusal.
- Separation of duties is enforced where it is usually lost — in the hand-off.
- A CEO → COO → buyer chain is one trace you can read in the chat.

## Disadvantages
- **An agent can always start a fresh chain as itself** (v1.1 residual).
  Signed hops stop anyone forging, dropping, reordering or editing a hop they
  did not sign, and stop a middle agent deleting a principal *before* it while
  keeping the origin. They cannot stop a compromised agent from discarding the
  whole chain and delegating on its own account; separation is then judged
  against that agent's own holdings (and the receiving tools' guard), so it
  gains nothing a design already let it do alone. A worker whose seed is
  stolen can sign as that agent; the seed is in that worker's environment
  only, and rotating it is re-running `env` after deleting the key.
- **A decision taken without a tool and without being named** (v1.1
  residual). A delegation that names a separated decision — explicitly, or by
  its id in the task text or inputs — is refused on both sides; one that
  describes it in other words ("settle what we owe Acme") is caught only when
  the receiver reaches for a tool that constitutes it. A judgement given in
  prose, with no tool call and no naming, is not seen by the bus at all: it
  is the agent's output, reviewed where outputs are (ADR-0070's approvals on
  the tools that act on it).
- Hops are signed with the agent's bus NKey: one key per agent for both
  transport identity and hop signatures. Separating them would mean a second
  per-agent secret for no difference in who holds it.
- Replies are not signed; the broker vouches for their sender (the subject)
  and a reply is only accepted for a handle the receiver holds for that
  sender. Replay protection is per worker process, in memory, bounded by the
  hop's expiry: a restarted worker would accept a replay of a hop it saw
  before the restart, within that hop's lifetime (JetStream's message-id
  de-duplication window, 120 s, still drops an identical re-publish).
- A member may message its leader (the reporting line) though the task named
  only leader → member; escalation up the tree is the tree's own affordance
  (ADR-0024), and without it a member could never report back unasked.
- The bus operator can publish anywhere under the tenant's subjects (that is
  how the receiver's refusal is demonstrated); it is an operator identity used
  only by `bus-init`, but it exists.

## Alternatives considered
- **Derive reach in the worker from the whole spec** — two implementations of
  the org chart, and the broker could not be told the same answer.
- **HTTP between workers with service tokens** — no durability, and every
  worker would need every neighbour's token; ADR-0114 gives none out.
- **Trust the body's `from`** — the reason subjects carry the sender.
- **Decentralized JWT auth (operator/account/user JWTs)** (v1.1) — gives
  revocation and per-account limits, but needs an operator key hierarchy, a
  resolver and JWTs re-issued whenever the design's edges change; plain NKey
  users keep the permissions in the one generated `nats.conf` the design
  already produces. **bcrypt-hashed passwords** — the broker could not use a
  hash to impersonate, but a password is still a bearer secret replayable by
  anything that sees it on the wire or in a worker's environment; an NKey
  proves possession by signing a nonce, and doubles as the hop-signing key.
- **Only the immediate sender signs** (v1.1) — the receiver would still be
  taking the earlier principals on that sender's word.
- **A2A inside the tenant** — ADR-0058 keeps internal delegation under the
  org chart; A2A is for agents we did not build.

## Verification
`tests/test_agent_bus.py`: the AYC edges (leader → member, flows by kind,
unit links, `partners_with` inert, mission windows, nothing undeclared), the
broker permissions rendered from them, the generated manifest, and both sides
of every refusal over a fake transport that enforces the broker's permissions
— including a spoofed sender, a delegation over a message-only edge, a forged
reply, depth, separation at send, at receipt and on the receiver's tools, the
CEO → COO → buyer chain as one trace, and the stub model's two-turn scripts.
v1.1 adds, over the same fake transport: a chain signed hop by hop and
verified end to end, a hop signed with the wrong key, a dropped principal
(broken hash link), text changed after signing, an unsigned, an expired and a
replayed hop, and a delegation naming a separated decision only in its text —
refused by sender and receiver; NKey round trip and checksum.
`tests/test_agent_bus_nats.py` runs the chain and the three refusals against a
real `nats-server` with the generated configuration and NKeys, plus a wrong
NKey refused by the broker and a forged hop refused by the receiver (skipped,
with the reason, when neither a `nats-server` nor Docker is available).
`tests/test_service_auth.py` checks the broker holds only public keys and
each worker only its own seed. `examples/ayc/end_to_end_messaging.py` runs it
on the Docker stack, with a forged hop chain (step 4b) and a wrong NKey and a
password login (step 5b) refused.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-24 | Broker identities are NKeys: the broker holds only public keys, each worker only its own seed (passwords retired). Every hop is signed with the sender's NKey and hash-chained; receivers verify the whole chain and judge separation by the verified principals. Delegations naming a separated decision in their text or inputs are refused. Residuals recorded: a fresh chain as oneself, unnamed decisions taken without a tool. |
| 1.0.0 | 2026-09-23 | Accepted. Compiled edges, per-agent broker users and subject permissions, send/delegate/check tools, receiver-side checks, separation across the chain, one trace per chain. |
