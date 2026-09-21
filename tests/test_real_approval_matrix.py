"""The mandate model against an approval matrix nobody here wrote.

Every worked example in this repository is one we authored, so every defect
they have found is a defect we were capable of imagining. This file tests the
mandate model against a **real, published** authorization matrix — a US
non-profit's `Exhibit 1 to Financial Policy`, transcribed verbatim in
`docs/fixtures/authorization-matrix.real.md` — to find the ones we were not.

Two kinds of test live here and both matter:

* what the model **expresses**, asserted so it keeps working;
* what the model **cannot express**, asserted so the gap is loud rather than
  a sentence in a document. Several of these assert that something wrong is
  currently accepted. When one of them fails, the model got better and the
  test should be rewritten, not deleted.
"""
from __future__ import annotations

import pytest

from orgagents.mandates import resolve
from orgagents.spec.model import (
    Mandate,
    Person,
    SystemSpec,
    condition_is_evaluable,
)
from orgagents.spec.validate import errors, validate_spec

# Every row of the published matrix, as a decision class.
ROWS = [
    "policy_organizational", "policy_privacy_security", "policy_customer_support",
    "policy_human_resources", "policy_marketing", "policy_technology",
    "policy_legal", "policy_communications", "policy_finance",
    "approve_annual_budget", "revise_budget_line_item", "revise_budget_categorical",
    "create_bank_account", "transfer_between_operating_accounts",
    "receive_federal_wire",
    "sign_disbursement_over_25k", "sign_disbursement_under_25k",
    "create_credit_account", "use_credit_account", "incur_indebtedness_over_25k",
    "approve_expenditure_over_250k", "approve_expenditure_5k_to_250k",
    "approve_expenditure_under_5k",
    "approve_employee_expense_reimbursement", "approve_employee_purchase_card",
    "approve_ceo_expenses",
    "approve_authorization_matrix", "approve_timesheet", "approve_strategic_plan",
]

# The six columns that are held by an individual. `Board of Directors` and
# `BOD Finance Committee` are bodies and are deliberately absent — see the
# gap tests below.
MATRIX = {
    "p_bod_chair": [
        "sign_disbursement_over_25k", "sign_disbursement_under_25k",
        "approve_ceo_expenses",
    ],
    "p_fincttee_chair": ["approve_ceo_expenses"],
    "p_ceo": [
        "policy_privacy_security", "policy_customer_support",
        "policy_human_resources", "policy_marketing", "policy_technology",
        "policy_legal", "policy_communications",
        "revise_budget_line_item", "revise_budget_categorical",
        "transfer_between_operating_accounts",
        "sign_disbursement_over_25k", "sign_disbursement_under_25k",
        "create_credit_account", "use_credit_account",
        "approve_expenditure_over_250k", "approve_expenditure_5k_to_250k",
        "approve_expenditure_under_5k",
        "approve_employee_expense_reimbursement",
    ],
    "p_cfo": [
        "revise_budget_line_item", "revise_budget_categorical",
        "transfer_between_operating_accounts", "receive_federal_wire",
        "sign_disbursement_over_25k", "sign_disbursement_under_25k",
        "use_credit_account", "approve_expenditure_under_5k",
        "approve_employee_expense_reimbursement",
        "approve_employee_purchase_card",
    ],
    "p_controller": [
        "use_credit_account", "approve_expenditure_under_5k",
        "approve_employee_purchase_card",
    ],
}

UNIT = {"p_bod_chair": "org", "p_fincttee_chair": "org", "p_ceo": "org",
        "p_cfo": "finance", "p_controller": "finance"}


def _spec(**overrides) -> SystemSpec:
    base = {
        "metadata": {"name": "matrix", "spec_version": "1.1.0",
                     "version": "0.1.0"},
        "decisions": [{"id": d} for d in ROWS],
        "organization": {
            "id": "org", "name": "The organization", "leader": "a_ceo",
            "members": [{"id": "a_ceo", "name": "ceo-agent",
                         "mandate": {"decisions": []}}],
            "teams": [{
                "id": "finance", "name": "Finance", "leader": "a_cfo",
                "members": [{"id": "a_cfo", "name": "cfo-agent",
                             "mandate": {"decisions": []}}],
            }],
        },
        "people": [
            {"id": pid, "contact": f"{pid}@example.org", "unit": UNIT[pid],
             "mandate": {"decisions": held}}
            for pid, held in MATRIX.items()
        ],
    }
    base.update(overrides)
    return SystemSpec.model_validate(base)


def _resolved(spec: SystemSpec):
    return resolve(spec.organization, [d.id for d in spec.decisions], spec.people)


# --------------------------------------------------------------------------
# What the model expresses
# --------------------------------------------------------------------------


def test_the_six_individual_columns_map_without_inventing_anything():
    spec = _spec()
    assert not errors(validate_spec(spec))
    resolved = _resolved(spec)
    for pid, held in MATRIX.items():
        assert resolved.for_person(pid).decisions == set(held), pid


def test_a_subordinate_may_hold_what_their_superior_does_not():
    """The shape I expected to break it, and it does not.

    Three published rows give a decision to a role below one that does not
    have it: only the CFO may receive a federal wire, only the CFO and the
    Controller may approve an employee purchase card, and only the Immediate
    Supervisor may approve a timesheet. Authority in a real matrix does not
    flow upward.

    It works because a team is a *scope* and a principal *acts* (ADR-0070):
    a person narrows against their unit, not against their manager, so the
    Finance unit may bound a decision its leader does not personally hold.
    """
    resolved = _resolved(_spec())
    assert resolved.holders("receive_federal_wire") == ["p_cfo"]
    assert "p_ceo" not in resolved.holders("approve_employee_purchase_card")
    assert resolved.for_person("p_cfo").covers("receive_federal_wire")


def test_escalation_reaches_the_person_and_invents_nobody_above_them():
    """Escalation lands on the CFO, and stops there.

    The agent holds nothing, so the search walks its line and finds the person
    attached to the Finance unit — ADR-0079 doing its job. What matters for
    this matrix is the second half: it does not keep walking to the chief
    executive, because the published matrix withholds this row from them, and
    a model that promoted it to the root would invent authority.
    """
    resolved = _resolved(_spec())
    assert resolved.holder("a_cfo", "receive_federal_wire") == "p_cfo"
    assert resolved.holders("receive_federal_wire") == ["p_cfo"]
    assert resolved.holder("a_ceo", "receive_federal_wire") is None, (
        "nothing above Finance holds it, and nothing invents a holder"
    )


def test_non_financial_rows_need_no_special_case():
    """Eight policy domains, a strategic plan and a timesheet.

    A `DecisionClass` is not a money thing, so the half of a real matrix that
    has no amount in it maps as easily as the half that does.
    """
    resolved = _resolved(_spec())
    assert resolved.for_person("p_ceo").covers("policy_privacy_security")
    assert not resolved.for_person("p_cfo").covers("policy_legal")


def test_the_matrix_approving_itself_is_a_shape_we_already_have():
    """`Authorization Matrix — Board of Directors ✓`.

    The document that governs approvals is itself approved, versioned and
    dated. That is ADR-0076/0077's platform policy, arrived at independently
    by somebody writing a finance policy.
    """
    assert "approve_authorization_matrix" in ROWS


def test_a_self_approval_prohibition_is_expressible_as_a_separation():
    """`Expense Reimbursements — CEO/ED`: approved by board officers, not the CEO.

    Expressible, with one catch worth stating: it only becomes *checkable* if
    incurring the expense is modelled as a decision somebody holds. The
    published matrix never says that, so importing it faithfully would
    produce the absence of a grant rather than a rule — and nothing would stop
    a later edit handing the CEO both.
    """
    spec = _spec(
        decisions=[{"id": "approve_ceo_expenses"}, {"id": "incur_ceo_expense"}],
        separations=[{"id": "no_self_approval",
                      "decisions": ["approve_ceo_expenses", "incur_ceo_expense"],
                      "reason": "The CEO may not approve their own expenses."}],
        people=[{"id": "p_ceo", "contact": "ceo@example.org", "unit": "org",
                 "mandate": {"decisions": ["approve_ceo_expenses",
                                           "incur_ceo_expense"]}}],
    )
    assert [f for f in validate_spec(spec) if f.code == "separation_violated"]


# --------------------------------------------------------------------------
# What the model cannot express. These assert the gap.
# --------------------------------------------------------------------------


def test_a_board_is_accepted_as_a_person_which_it_is_not():
    """Two of the eight columns are **bodies**, not people.

    `Board of Directors` and `BOD Finance Committee` decide by quorum and
    vote. ADR-0079 gives us one principal kind for humans and it has a name
    and a contact address, so a board can only be modelled by pretending it is
    a person — and the model accepts the pretence without complaint.
    """
    board = Person(id="p_board", name="Board of Directors",
                   contact="board@example.org",
                   mandate=Mandate(decisions=["approve_annual_budget"]))
    assert board.contact, "a fabricated mailbox for a committee"
    assert not hasattr(board, "quorum")
    assert not hasattr(board, "members")


def test_immediate_supervisor_can_only_be_modelled_as_one_fixed_person():
    """The eighth column is a **relative** principal.

    `Immediate Supervisor ✓` on `Time Sheets` means *the requester's own*
    supervisor, which is a different human for every request. A `Person` has
    an id and is one principal, so the honest transcription of this column is
    impossible: either one invented person approves every timesheet in the
    organization, or the row is dropped.

    The mechanism exists for agents — `MandateMap.holder` walks the manager
    chain — and there is no way to *declare* it.
    """
    supervisor = Person(id="p_immediate_supervisor", name="Immediate Supervisor",
                        mandate=Mandate(decisions=["approve_timesheet"]))
    assert supervisor.id == "p_immediate_supervisor"
    assert supervisor.contact == "", "nobody to write down, because it is nobody"
    assert "p_immediate_supervisor" not in MATRIX, (
        "the honest mapping drops the row rather than inventing a person"
    )


def test_dual_approval_is_not_expressible_at_all():
    """`Expenditure over $250,000 — Dual approval required — BOD ✓ CEO ✓`.

    A `Mandate` says which decisions one principal holds. There is nowhere to
    say a decision needs *two* of them, which two, or that one must be a board
    member (footnote 2). The published matrix uses the same notation — several
    ticks on one row — for "any one of" (`1 Signature`), "any two of, one from
    the board" (`2 Signatures`) and "both" (`Dual approval required`), and
    only the free-text note distinguishes them.
    """
    assert sorted(Mandate.model_fields) == ["conditions", "decisions",
                                            "enforcement"]
    for key in ("requires_two_approvers", "approvers_min", "quorum"):
        assert not condition_is_evaluable(key), key


def test_a_dual_approval_condition_is_accepted_and_read_by_nothing():
    """Writing it down anyway produces a decorative field.

    `requires_approval` is in `PLATFORM_EVALUATED_CONSTRAINTS` for a
    *capability*, and the mandate-condition grammar does not parse it, so on a
    mandate it is neither refused by the validator nor evaluated at run time.
    """
    spec = _spec(people=[{
        "id": "p_ceo", "contact": "ceo@example.org", "unit": "org",
        "mandate": {"decisions": ["approve_expenditure_over_250k"],
                    "conditions": {"requires_approval": True}}}])
    assert not [f for f in validate_spec(spec)
                if f.code == "unenforceable_platform_control"]


def test_the_published_threshold_gap_passes_silently():
    """The matrix's own bands are `> $5,000 ≤ $250,000` and `< $5,000`.

    Exactly $5,000 is in neither, and so is exactly $25,000 between
    `> $25,000` and `< $25,000`. Two coverage gaps in a real, board-approved
    control document — and nothing in our model looks at whether a decision's
    bands cover the number line.
    """
    spec = _spec(people=[
        {"id": "p_hi", "contact": "hi@example.org", "unit": "org",
         "mandate": {"decisions": ["approve_expenditure_5k_to_250k"],
                     "conditions": {"min_value": 5000, "max_value": 250000}}},
        {"id": "p_lo", "contact": "lo@example.org", "unit": "org",
         "mandate": {"decisions": ["approve_expenditure_under_5k"],
                     "conditions": {"max_value": 5000}}},
    ])
    assert not [f for f in validate_spec(spec) if "band" in f.code
                or "coverage" in f.code or "gap" in f.code]


def test_an_aggregate_bound_passes_the_evaluable_check_it_should_fail():
    """Footnote 3: the threshold is on *total commitment per procurement*.

    `condition_is_evaluable` asks whether the grammar can **parse** the key.
    ADR-0073 needs it to answer whether this platform can **compute** the
    quantity. `max_value` (this call's own amount) and
    `max_total_per_procurement` (a sum across calls this platform never sees)
    are indistinguishable to a prefix match, so a control claimed for the
    platform is checked against a number the calling agent supplies.

    It is not silently ignored — an absent field is a refusal — which makes
    this narrower than the decorative-field defect and the same shape: it
    reads as a bound on a total and is a bound on a claim.
    """
    assert condition_is_evaluable("max_value")
    assert condition_is_evaluable("max_total_per_procurement")
    assert condition_is_evaluable("max_rate_per_minute"), (
        "deliberately excluded from PLATFORM_EVALUATED_CONSTRAINTS, and the "
        "mandate grammar accepts it — the two gates disagree"
    )


def test_sub_delegation_has_no_model():
    """Footnote 1: `May be delegated to other staff by authorized party`.

    A person handing part of their authority to an unnamed subordinate, under
    a policy we do not read. ADR-0064 holds open whether an *agent* may act as
    a person; this is a person delegating to a person, and nothing addresses
    it.
    """
    assert not hasattr(Person, "may_delegate")
    assert "delegates_to" not in Person.model_fields


def test_signing_and_approving_are_one_thing_here_and_two_there():
    """Footnote 4: `Contract Signatures can be delegated by the Principal Officer`.

    The matrix separates the authority to approve from the authority to sign,
    and delegates them separately. A `DecisionClass` is one thing, so the
    faithful transcription above had to collapse them.
    """
    assert "approve_expenditure_over_250k" in ROWS
    assert not any(row.startswith("sign_contract") for row in ROWS)


def test_an_absence_exception_cannot_be_written_down():
    """Footnote 2: `(Exceptions allowed for Board Member absences)`.

    A control that relaxes when somebody is away. ADR-0079 listed
    out-of-office as the scope creep its rule 3 exists to refuse; it is in a
    real board-approved matrix, as a named exception to a signature rule.
    """
    assert "working_hours" in Person.model_fields, (
        "we model when a person is reachable..."
    )
    assert not any("absence" in f or "delegate" in f
                   for f in Person.model_fields), (
        "...and not what their authority does while they are not"
    )
