---
id: ADR-0080
title: Content carries its classification and egress is checked against it
status: Accepted
version: 1.0.0
date: 2026-09-21
updated: 2026-09-21
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [spec, runtime, security]
workstreams: [WS-003, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0008, ADR-0017, ADR-0030, ADR-0035, ADR-0050, ADR-0065, ADR-0067, ADR-0072, ADR-0073]
tags: [security, data, information-flow, egress]
---

# ADR-0080: Content carries its classification and egress is checked against it

## Context
Permissions answer one question: *may this agent read this data class*. They
answer it well — deny by default, resolved once at the phase gate, narrowing
down the tree. They answer nothing about what happens to the value afterwards.

An agent that may read `hr_pii` and may call an external endpoint can carry one
into the other, and no rule we have notices. There is a classification check on
the egress path, and it is worth being precise about why it does not close
this, because it looks like it does:

```python
# runtime/endpoints.py
payload_data_classes: Sequence[str] = (),
...
refused = [dc for dc in payload_data_classes if dc not in sendable]
```

The classes are a **parameter the caller supplies**, and the default is empty.
Run the same payload both ways against an endpoint that may send only
`public_notes`:

| Call | `data_classification` |
|---|---|
| `{"ssn": "123-45-6789", "salary": 91000}`, no annotation | **passes** |
| the same payload, `payload_data_classes=["hr_pii"]` | refused, `data_class_refused` |

So the check is exactly as good as the memory of whoever wrote the call site,
and forgetting is silent and is the default. That is the decorative-field
defect this platform has spent its time removing, wearing a security check's
clothes.

The same gap has a second face. ADR-0035 treats an external endpoint's answer
as data rather than instruction, and `AgentEndpoint.treat_output_as_data`
carries it — per endpoint, as a flag somebody sets. Whether a particular
*value* in a prompt came from a trusted place or from a web page nobody
controls is not represented at all, so "this text is untrusted" is a property
of a connection rather than of the content that came through it.

Microsoft Agent Framework's `security.py` is the worked answer to both, read
directly for LANDSCAPE §9: content carries an integrity label and a
confidentiality label, labels combine along a dataflow, and the flow is checked
at the exfiltration boundary. The mechanism is not novel and it is not ours to
invent; the question is what shape it takes in a platform whose whole premise
is that policy is declared in a spec and resolved before anything runs.

## Decision
**Content carries a label. The label travels with it. Egress and delegation are
checked against the label, not against an annotation somebody remembered to
write.**

1. **Confidentiality is the set of data classes a value is derived from**, not
   a new taxonomy. We already have `DataClass` with a scope, groups and egress
   rules; a second vocabulary beside it (public / private / secret) would be a
   thing that can disagree with the first. This is the rule ADR-0069 rule 4
   applied to labels: reuse the plane, do not invent one next to it.
2. **Integrity is two-valued: `trusted` or `untrusted`.** Trusted is content
   this organization's own systems and people produced. Untrusted is everything
   that came from outside a boundary we govern — an external or partner
   endpoint, a web page, an inbound channel message, an MCP server's result.
   `treat_output_as_data` becomes the *source* of a label rather than a
   per-endpoint switch.
3. **Labels combine on the join, and only ever widen.** A result derived from
   two sources carries the union of their data classes and `untrusted` if
   either was. Narrowing is not an operation this structure has, which is what
   makes it correct by construction — the same reason a mandate's conditions
   chain rather than merge (ADR-0065).
4. **The label is produced at the tool boundary and nowhere else.** The harness
   already wraps every tool an agent may call (`HarnessBuilder.guarded`); that
   is where a result gets its label and where a call's arguments get checked
   against theirs. Not inside a framework's middleware: a check that lives in
   one framework means nothing to the next (ADR-0067 rule 5).
5. **`payload_data_classes` stops being a parameter.** The egress check reads
   the label of what is being sent. A call site that supplies nothing gets the
   agent's accumulated label, not an empty set, so forgetting fails **closed**.
6. **Declassification is a decision, not a flag.** Lowering a label — asserting
   that a summary of `hr_pii` is no longer `hr_pii` — is exactly the kind of
   judgement the mandate model exists for. It is a declared `DecisionClass`, an
   agent may do it only if its effective mandate covers it, and it obeys the
   autonomy posture like anything else (ADR-0072). An organization that
   declares no such decision cannot declassify at all, which is the right
   default.
7. **The spec declares the policy; the label exists at run time.** A data class
   gains what it may reach and under what integrity; nothing about a *value*
   appears in a spec, because values do not exist at design time. The phase
   gate checks the policy is coherent; the runtime carries the labels.
8. **This is not taint tracking through a model, and the record says so.** Once
   content is in a prompt, what the model writes next is beyond our reach. The
   label is carried at the tool boundary and is therefore a **conservative
   over-approximation**: an agent that read `hr_pii` is treated as holding it
   until something declassifies. Any claim stronger than that would be false.

## Scope
The harness tool boundary, the egress path in `runtime/endpoints.py`, the
delegation path, and the data-class declarations in the spec that state the
policy. It does not change the permission resolver, mandates, placement, or
what an execution sandbox does. It does not quarantine untrusted content behind
handles — see the alternatives.

## Implementation
**Not implemented.** Staged, because rule 5 is the part with teeth and rule 6
is the part that makes it liveable:

* **M1 — the label.** A `Label(data_classes: frozenset[str], integrity)` with a
  join, and a `Labelled` result the harness returns from a guarded tool. No
  check yet, so nothing can break; the label is observable and testable on its
  own.
* **M2 — sources.** Every guarded tool labels its result from what it read: a
  capability from its data classes, an endpoint from its trust, a channel
  message from its origin. The accumulated label lives on the run.
* **M3 — egress reads the label.** `payload_data_classes` becomes a fallback
  rather than the input, and an unannotated call carries the run's label. This
  is the change that turns the table in the Context around, and it is the one
  that will refuse calls that used to pass.
* **M4 — declassification as a decision.** A `DecisionClass`, checked against
  the effective mandate at the boundary, with the posture applied.
* **M5 — the designer shows it.** Which data classes each agent can accumulate,
  and which of its endpoints that makes unreachable without declassification.
  This is a design-time answer to a run-time mechanism, and it is what makes
  the whole thing reviewable before deployment rather than discovered after.

M3 is a breaking change to a security check's behaviour and belongs behind the
platform policy's severity mechanism (ADR-0077), so an organization can adopt
it as a warning before it refuses.

## Timeline
Phase 6, after the alpha. Nothing here blocks it.

## Advantages
- It closes a hole that is currently open by default and silent when it fires
  the wrong way. Today the check passes unless somebody annotated.
- It reuses `DataClass` rather than inventing a taxonomy beside it, so there is
  one answer to "what is this data" and not two that can drift.
- Forgetting fails closed, which inverts the current default.
- "Untrusted" becomes a property of content rather than of a connection, which
  is what ADR-0035 wanted and could not express.
- Declassification lands in the authority model instead of becoming a flag,
  so the question "who may decide this is no longer PII" has the same answer
  shape as every other decision this platform governs.
- It is the one axis Microsoft Agent Framework has and we do not, and it is a
  gap that exists whatever runtime we bind to.

## Disadvantages
- **Label creep is the known failure mode and we will get it.** Labels only
  widen, so an agent that touches the ledger once carries `ledger` for the rest
  of the run and its endpoints start refusing. Rule 6 is the release valve and
  it is deliberately expensive — a declared decision, a mandate, a posture —
  so the first team that hits this will experience the design as an obstacle
  and will be right.
- **A conservative over-approximation refuses work that is fine.** A summary
  that contains no PII still carries the PII label. We cannot tell the
  difference, and saying we can would be the lie rule 8 exists to prevent.
- **It is a run-time mechanism in a design-time platform.** Everything else
  here is resolved once at the phase gate and written into the IR. A label
  exists only while something runs, in the least-exercised part of this
  codebase, and a bug in it fails in production rather than in review.
- **Every guarded tool result grows a wrapper.** That is plumbing through the
  harness, the adapters and the bus, and a path that forgets to carry the label
  silently launders it — the exact defect this record is fixing, reintroduced
  one call site at a time.
- **It does not stop the model.** An agent that may reach an external endpoint
  and has PII in its context can be talked into putting it in a URL path, a
  tool argument we do not classify, or a message to another agent. Labels at
  the tool boundary narrow that, and do not close it.
- **Two integrity values will not be enough**, and the third will be argued
  about. Partner data is neither this organization's nor a web page's.

## Alternatives considered
- **Leave it as a declared annotation.** Free, and the check reads as enforced
  while passing by default — precisely what ADR-0073 refuses for controls, and
  what the table in the Context measures.
- **Quarantine untrusted content behind handles**, as MAF's `ContentVariableStore`
  and CaMeL do: untrusted text never reaches the model as text, only as a
  `var_<id>` a quarantined model may inspect. Strictly stronger, and it changes
  how every tool result reaches every model in every adapter, which is a much
  larger bet on a mechanism we have not run. Recorded as the better answer we
  are not building yet; nothing here forecloses it.
- **A new confidentiality taxonomy beside data classes** (public / private /
  secret). Familiar and small, and it creates two classifications for one value
  that can disagree, which is the defect rule 1 exists to avoid.
- **Per-endpoint allowlists and discipline.** What we have. It depends on the
  author of each call site and the default is the unsafe one.
- **Enforce it in framework middleware.** Cheap for one runtime, and it means
  nothing for the next one, which ADR-0067 rule 5 already settled.

## Verification
Tests assert: a label joins to the union of its sources' data classes and to
`untrusted` if either source was; a call that supplies no annotation is checked
against the run's accumulated label rather than against an empty set; the
payload in this record's Context table is refused without anybody annotating
it; a result from an external endpoint is labelled `untrusted` whatever the
endpoint's flags say; declassification by an agent whose mandate does not cover
it is refused and names the decision; an organization that declares no
declassification decision cannot lower a label at all; and a guarded tool that
returns an unlabelled result is a failure rather than a pass.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Accepted. Confidentiality is the set of data classes a value derives from, integrity is trusted/untrusted, labels join and only widen, the tool boundary produces them, egress reads them instead of an annotation, and declassification is a decision under the mandate model. Not taint tracking through a model, and not quarantine behind handles. |
