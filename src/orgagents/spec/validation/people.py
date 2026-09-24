"""People as principals, and separation of duties over them (ADR-0079, ADR-0070).

A person is a principal for authority and never for access. Declared once,
so that one human is one principal — without which no separation check over
people can work.
"""
from __future__ import annotations

from ..model import HumanRole
from .authority import check_mandate
from .context import ValidationContext
from .registry import rule

SECTION = "people as principals"


@rule(SECTION, {"duplicate_person", "person_holds_access", "unknown_reference",
                "undeclared_decision"})
def one_human_one_principal(ctx: ValidationContext) -> None:
    spec, err = ctx.spec, ctx.err
    seen_people: set[str] = set()
    for person in spec.people:
        if person.id in seen_people:
            err(
                "duplicate_person",
                f"person '{person.id}' is declared more than once. One human is "
                "one principal, or the checks below are checking copies",
                person.id,
            )
        seen_people.add(person.id)
        claims = person.claims_access
        if claims:
            err(
                "person_holds_access",
                f"person '{person.id}' declares {', '.join(claims)}. A person's "
                "access is not mediated here — they sign in under their "
                "employer's IAM — so a permission this platform cannot enforce "
                "is worse than none. Declare what they may *decide* as a "
                "mandate instead (ADR-0079)",
                person.id,
            )
        if person.unit and not any(t.id == person.unit for t in ctx.teams):
            err(
                "unknown_reference",
                f"person '{person.id}' is attached to unit '{person.unit}', "
                "which the organization does not contain",
                person.id,
            )
        check_mandate(ctx, person.mandate, person.id, "person")

    by_contact: dict[str, list[str]] = {}
    for person in spec.people:
        if person.contact:
            by_contact.setdefault(person.contact.lower(), []).append(person.id)
    for contact, ids in by_contact.items():
        if len(ids) > 1:
            err(
                "duplicate_person",
                f"{sorted(ids)} share the contact '{contact}', so one human is "
                "declared as several principals. Separation of duties cannot "
                "see past that (ADR-0079)",
                sorted(ids)[0],
            )


@rule(SECTION, {"unknown_reference"})
def pairings_name_declared_people(ctx: ValidationContext) -> None:
    for agent in ctx.agents:
        for human in agent.humans:
            if human.person and not any(p.id == human.person for p in ctx.spec.people):
                ctx.err(
                    "unknown_reference",
                    f"agent '{agent.id}' is paired with person "
                    f"'{human.person}', which the spec does not declare",
                    agent.id,
                )


@rule(SECTION, {"owner_approves_own_agent"})
def owner_does_not_approve_own_agent(ctx: ValidationContext) -> None:
    # Four-eyes, expressed where it can be checked (ADR-0079 rule 5). The
    # person accountable for an agent cannot also be the one who approves what
    # it raises; ADR-0072 rule 3 could only approximate this because a person
    # was not an identity.
    for agent in ctx.agents:
        owners = {
            h.principal() for h in agent.humans if HumanRole.OWNER in h.roles
        }
        for human in agent.humans:
            if HumanRole.APPROVER not in human.roles:
                continue
            who = human.principal()
            if who and who in owners:
                ctx.err(
                    "owner_approves_own_agent",
                    f"agent '{agent.id}' is owned and approved by the same "
                    f"person ('{who}'), so the approval is the raiser's own "
                    "signature. Name an approver who does not own it "
                    "(ADR-0079)",
                    agent.id,
                )


@rule(SECTION, {"undeclared_decision", "separation_without_conflict",
                "separation_violated"})
def separation_of_duties(ctx: ValidationContext) -> None:
    # Separation of duties (ADR-0070). Checked over *effective agent*
    # mandates, because an agent is what acts: a team's mandate bounds its
    # members and is exercised by nobody.
    spec, err, warn = ctx.spec, ctx.err, ctx.warn
    resolved = ctx.separation_mandates
    if resolved is None:
        return
    for rule_ in spec.separations:
        unknown = [d for d in rule_.decisions if d not in ctx.declared_decisions]
        for d in unknown:
            err(
                "undeclared_decision",
                f"separation '{rule_.id}' names decision class '{d}', which "
                "the spec does not declare",
                rule_.id,
            )
        if len(rule_.decisions) < 2:
            warn(
                "separation_without_conflict",
                f"separation '{rule_.id}' names fewer than two decisions, so "
                "nothing can violate it",
                rule_.id,
            )
    for person_id, effective in resolved.people.items():
        for rule_ in spec.separations:
            held = sorted(set(rule_.decisions) & effective.decisions)
            if len(held) > 1:
                err(
                    "separation_violated",
                    f"person '{person_id}' holds {held}, which separation "
                    f"'{rule_.id}' forbids"
                    + (f": {rule_.reason}" if rule_.reason else "")
                    + ". People are the principal in most real frauds, so "
                    "a rule that does not cover them does not cover the "
                    "case it was written for (ADR-0079)",
                    person_id,
                )
    for agent_id, effective in resolved.agents.items():
        for rule_ in spec.separations:
            held = sorted(set(rule_.decisions) & effective.decisions)
            if len(held) > 1:
                err(
                    "separation_violated",
                    f"agent '{agent_id}' holds {held}, which separation "
                    f"'{rule_.id}' forbids"
                    + (f": {rule_.reason}" if rule_.reason else "")
                    + ". An agent inheriting its unit's mandate holds "
                    "everything beneath it, so a leader over both sides of "
                    "a control must declare a narrower mandate of its own",
                    agent_id,
                )
