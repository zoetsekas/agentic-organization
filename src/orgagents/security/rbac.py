"""Deny-by-default authorization (ADR-0008).

Three properties the rest of the platform relies on:

* **Default deny.** A request with no matching allow is denied.
* **Deny wins.** A matching deny cannot be overridden by any allow, at any
  layer, for any subject — including a team leader.
* **Auditable.** Every decision names the rule that produced it, so
  "why was this refused?" is answerable from the trace alone.

The engine evaluates the *resolved* permission set from the IR plus the spec's
policy rules. It never re-derives inheritance; that happened once, in the
compiler.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Optional

from ..spec.model import POLICY_CONDITION_KEYS, Action, Effect, Permission, PolicyRule, ResourceKind


@dataclass(frozen=True)
class Subject:
    """Who is asking: an agent, with the attributes policies may condition on."""

    id: str
    team_id: str = ""
    team_path: tuple[str, ...] = ()
    role_ids: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    is_leader: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)

    def identifiers(self) -> set[str]:
        return {self.id, self.team_id, *self.team_path, *self.role_ids}


@dataclass(frozen=True)
class Request:
    """What is being asked: an action on a resource, in a context."""

    action: Action
    resource_kind: ResourceKind
    resource: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    allowed: bool
    reason: str
    rule_id: Optional[str] = None
    matched_permission: Optional[str] = None

    def __bool__(self) -> bool:
        return self.allowed


def _matches(pattern: str, value: str) -> bool:
    return pattern == "*" or fnmatch.fnmatch(value, pattern)


class UnevaluableCondition(ValueError):
    """A condition names a key this evaluator cannot check.

    Raised rather than ignored. Ignoring it is not neutral: on an `unless`
    guard it disapplies the rule, so one transposed letter
    (`reqiures_approval`) turned a deny on PII into an allow.
    """


def unknown_condition_keys(conditions: dict[str, Any]) -> list[str]:
    """Keys in a condition set that nothing here evaluates."""
    return sorted(set(conditions or {}) - POLICY_CONDITION_KEYS)


def _conditions_hold(conditions: dict[str, Any], subject: Subject,
                     request: Request) -> bool:
    """Evaluate attribute conditions against the subject and the request.

    Supported forms, kept deliberately small so effective permissions stay
    computable ahead of time:

    ``max_delegation_depth``  request depth must not exceed the value
    ``requires_approval``     context must carry an approval
    ``environments``          request environment must be in the list
    ``data_classes``          request data class must be in the list
    ``groups``                subject must hold one of the groups
    ``time_window``           request hour must fall inside ``[start, end)``
    """
    unknown = unknown_condition_keys(conditions)
    if unknown:
        # Not `False`: the safe direction differs by effect — an ignored
        # condition under-applies an allow and over-applies a deny, and an
        # ignored `unless` switches a deny off. The only answer that is safe
        # whatever it guards is "this rule cannot be evaluated".
        raise UnevaluableCondition(
            f"condition key(s) {unknown} are not evaluated by this platform; "
            f"it understands {sorted(POLICY_CONDITION_KEYS)}"
        )
    ctx = request.context
    if "max_delegation_depth" in conditions:
        if int(ctx.get("delegation_depth", 0)) > int(conditions["max_delegation_depth"]):
            return False
    if conditions.get("requires_approval") and not ctx.get("approved"):
        return False
    if "environments" in conditions:
        if ctx.get("environment") not in conditions["environments"]:
            return False
    if "data_classes" in conditions:
        if ctx.get("data_class") not in conditions["data_classes"]:
            return False
    if "groups" in conditions:
        if not set(conditions["groups"]) & set(subject.groups):
            return False
    if "time_window" in conditions:
        start, end = conditions["time_window"]
        hour = int(ctx.get("hour", -1))
        if not (start <= hour < end):
            return False
    return True


class PolicyEngine:
    """Evaluates a resolved permission set plus explicit policy rules."""

    def __init__(
        self,
        permissions: dict[str, list[Permission]],
        rules: Optional[list[PolicyRule]] = None,
    ) -> None:
        # agent id -> its effective permissions, resolved by the compiler.
        self.permissions = permissions
        self.rules = rules or []

    # -- evaluation --------------------------------------------------------

    def decide(self, subject: Subject, request: Request) -> Decision:
        try:
            return self._decide(subject, request)
        except UnevaluableCondition as exc:
            # A rule we cannot evaluate is a rule we cannot honour, and the
            # phase gate should have refused this spec long before here
            # (`unknown_policy_condition`). Reaching it at run time means a
            # rule arrived from somewhere the gate did not see, so the request
            # is refused rather than decided on a partial reading.
            return Decision(False, f"policy cannot be evaluated: {exc}")

    def _decide(self, subject: Subject, request: Request) -> Decision:
        # 1. An explicit deny ends the evaluation, whoever is asking.
        for rule in self.rules:
            if rule.effect is Effect.DENY and self._rule_matches(rule, subject, request):
                return Decision(
                    False,
                    f"denied by policy '{rule.id}'"
                    + (f": {rule.description}" if rule.description else ""),
                    rule_id=rule.id,
                )

        # 2. A resolved permission allows it.
        for perm in self.permissions.get(subject.id, []):
            if (
                perm.action is request.action
                and perm.resource_kind is request.resource_kind
                and _matches(perm.resource, request.resource)
                and _conditions_hold(perm.conditions, subject, request)
            ):
                return Decision(
                    True,
                    f"granted by permission {perm.key()}",
                    matched_permission=perm.key(),
                )

        # 3. An explicit allow rule can grant what no role did.
        for rule in self.rules:
            if rule.effect is Effect.ALLOW and self._rule_matches(rule, subject, request):
                return Decision(True, f"allowed by policy '{rule.id}'", rule_id=rule.id)

        # 4. Default deny.
        return Decision(
            False,
            f"no permission grants {request.action.value} on "
            f"{request.resource_kind.value} '{request.resource}'",
        )

    def _rule_matches(self, rule: PolicyRule, subject: Subject, request: Request) -> bool:
        if rule.actions and request.action not in rule.actions:
            return False
        if rule.resource_kinds and request.resource_kind not in rule.resource_kinds:
            return False
        if not any(_matches(r, request.resource) for r in rule.resources or ["*"]):
            return False
        subjects = rule.subjects or ["*"]
        if not any(
            s == "*" or any(_matches(s, ident) for ident in subject.identifiers())
            for s in subjects
        ):
            return False
        if not _conditions_hold(rule.conditions, subject, request):
            return False
        # An `unless` guard disapplies the rule where it holds, which is how
        # "deny everywhere except the clean room" is written.
        if rule.unless and _conditions_hold(rule.unless, subject, request):
            return False
        return True

    # -- helpers -----------------------------------------------------------

    def effective(self, agent_id: str) -> list[Permission]:
        return list(self.permissions.get(agent_id, []))

    def explain(self, subject: Subject, request: Request) -> dict[str, Any]:
        decision = self.decide(subject, request)
        return {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "rule": decision.rule_id,
            "permission": decision.matched_permission,
            "subject": subject.id,
            "request": {
                "action": request.action.value,
                "resource_kind": request.resource_kind.value,
                "resource": request.resource,
            },
        }
