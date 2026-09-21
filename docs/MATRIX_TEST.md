# The mandate model against a real approval matrix

**Question.** Every worked example in this repository is one we wrote, so every
defect they have found is a defect we were capable of imagining. Can the
mandate model hold a real organization's delegation of authority **without
inventing anything**?

**Method.** A published authorization matrix nobody here wrote — a US
non-profit's `Exhibit 1 to Financial Policy`, board-approved, two versions
available — transcribed verbatim into
[`docs/fixtures/authorization-matrix.real.md`](fixtures/authorization-matrix.real.md)
with no tidying, then mapped into the spec. Both the fit and the failures are
asserted in `tests/test_real_approval_matrix.py`, so this page cannot rot into
a claim: if the model improves, a gap test fails and has to be rewritten.

**Verdict: the model holds about two thirds of it.** Twenty-nine rows, eight
columns. Six of the eight columns and all twenty-nine rows map cleanly. Two
columns and four footnotes do not, and three of those are not small.

---

## What mapped, including one I expected to break it

**The grid itself.** One `DecisionClass` per row, one `Person` with a `Mandate`
per column. Twenty-nine decisions across five people, zero validation errors,
no field bent to fit.

**Authority that does not flow upward.** Three rows give a decision to a role
whose superior does not have it: only the CFO may receive a federal wire, only
the CFO and Controller may approve an employee purchase card, only the
Immediate Supervisor may approve a timesheet. I expected this to break the
model, because a mandate narrows downward and a child's cannot exceed its
line's.

It works, and the reason is a decision we made for a different purpose.
**Teams are scopes and principals act** (ADR-0070): a person narrows against
their *unit*, not against their manager, so the Finance unit can bound a
decision its own leader does not personally hold. Escalation then behaves
correctly in both directions — a Finance agent's escalation reaches the CFO,
and nothing invents a holder above them.

**Non-financial rows need no special case.** Eight policy domains, a strategic
plan, a timesheet. Roughly half a real matrix has no money in it, and a
`DecisionClass` is not a money thing.

**The matrix approves itself.** `Authorization Matrix — Board of Directors ✓`.
The document governing approvals is itself versioned, dated and board-approved
— ADR-0076/0077's platform policy, arrived at independently by somebody writing
a finance policy. That is corroboration, not coincidence.

**Self-approval is expressible**, with a catch. `Expense Reimbursements —
CEO/ED` is approved by board officers and not by the chief executive. As a
`SeparationRule` this is checkable — but only if incurring the expense is
modelled as a decision somebody holds, and the published matrix never says
that. A faithful import produces the *absence of a grant* rather than a rule,
and nothing stops a later edit handing the CEO both.

---

## What did not map

### 1. Two of the eight columns are bodies, not people

`Board of Directors` and `BOD Finance Committee` decide by quorum and vote.
ADR-0079 gives exactly one principal kind for humans, with a name and a contact
address. A board can only be modelled by pretending it is a person — and the
model **accepts the pretence silently**, complete with a fabricated mailbox.

This is the most serious finding, because those two columns carry the
organization's largest decisions: the annual budget, indebtedness over $25,000,
bank account creation, the strategic plan, and the matrix itself. The model is
weakest exactly where the authority is greatest.

### 2. `Immediate Supervisor` is a relative principal

It means *the requester's own* supervisor — a different human per request. A
`Person` has an id and is one principal, so the honest transcription either
invents one person who approves every timesheet in the organization, or drops
the row. We dropped it.

Galling detail: the mechanism exists. `MandateMap.holder` already walks a
manager chain. There is simply no way to **declare** "whoever this person
reports to".

### 3. Dual approval and N-of-M are not expressible at all

A `Mandate` has three fields — `decisions`, `conditions`, `enforcement` — and
says what *one* principal holds. Nowhere can it say a decision needs two of
them, which two, or that one must be a board member.

The matrix needs all three shapes, and uses **the same notation for all of
them**: several ticks on a row means "any one of" (`1 Signature`), "any two of,
one from the board" (`2 Signatures`, footnote 2) and "both" (`Dual approval
required`) — distinguished only by free text in the Additional Notes column.
No automatic import can be correct; each row needs a human to say which it is.

Writing `requires_approval: true` into a mandate's conditions is accepted, and
is read by nothing: it is in `PLATFORM_EVALUATED_CONSTRAINTS` for a
*capability*, and the mandate-condition grammar does not parse it. A decorative
field, in the newest part of the model.

### 4. Four footnotes, four gaps

| Footnote | What it says | Status |
|---|---|---|
| (1) | `May be delegated to other staff by authorized party` | No model. ADR-0064 covers agent-acts-as-person; person-delegates-to-person is untouched |
| (2) | `Exceptions allowed for Board Member absences` | No model. ADR-0079 named out-of-office as scope creep to refuse; here it is a board-approved exception to a signature rule |
| (3) | `Threshold applies to total commitment of funds per procurement` | Deliberately declined (ADR-0073) — but see the defect below |
| (4) | `Contract Signatures can be delegated by the Principal Officer` | Signing and approving are separate authorities, separately delegable. A `DecisionClass` is one thing, so the transcription collapsed them |

---

## Two defects in our own code, found by the exercise

**The matrix has a coverage gap, and we cannot see it.** Its bands are
`> $5,000 ≤ $250,000` and `< $5,000`. Exactly $5,000 is in neither. Same at
$25,000, between `> $25,000` and `< $25,000`. Two holes in a real,
board-approved control document — and nothing in our model asks whether a
decision's bands cover the number line. This is a check we should have and do
not, and it is the kind an importer would earn its keep on immediately.

**`condition_is_evaluable` answers the wrong question.** It asks whether the
grammar can *parse* a key; ADR-0073 needs to know whether this platform can
*compute* the quantity. A prefix match cannot tell `max_value` (this call's own
amount, genuinely checkable here) from `max_total_per_procurement` (a sum
across calls this platform never sees). Both pass, so a control claimed for the
platform is checked against a number the calling agent supplies.

It is narrower than a decorative field — an absent field is a refusal, so the
bound is not ignored — and it is the same shape: it **reads as a bound on a
total and is a bound on a claim**. Worse, the two control-ownership gates
disagree. The capability path uses an explicit allowlist that deliberately
excludes `rate_per_minute`; the mandate path uses the prefix grammar, which
accepts `max_rate_per_minute` happily.

---

## What this settles

The distinctive claim survives contact with a real document, and is smaller
than it looked. The model expresses **who may decide what, bounded by the unit
they sit in** — and a real matrix is largely that, which is the good news. What
it does not express is **collective decision-making, relative principals, and
how many signatures a thing needs** — and a real matrix is substantially that
too, disproportionately at the top.

The specific correction to make: nothing here suggests the decision model is
wrong. It suggests it is **incomplete in three named ways**, each of which is a
decision rather than a bug. That is a much better position than the worked
examples could establish on their own, because we did not choose this document
and it did not flatter us.

One caveat on the evidence. This is a small non-profit's matrix: eight columns,
twenty-nine rows, one page. A bank's delegation of authority runs to hundreds of
rows with product, entity, jurisdiction and risk-rating dimensions the
`(decision, principal, condition)` shape has never been asked to carry. The
next test should be a larger one, and the honest expectation is that it finds
more.

## Sources

- [Authorization Matrix, version V2022a](https://c4-media.s3.amazonaws.com/wp-content/uploads/2022/10/20123120/20221024_Delegation_of_Authority_Matrix.pdf)
- [Authorization Matrix, version V2020a](https://c4-media.s3.amazonaws.com/wp-content/uploads/2021/01/07103804/20210111_Copy_of_Delegation_of_Authority_Matrix.pdf)
