"""The Assurance profile (ADR-0112): what keeps a design safe and releasable —
guardrails, output contracts, evaluations, the lifecycle and its gates,
compliance, observability, resilience, budgets and model policy."""
from __future__ import annotations

from .uml import DataType, Draw, Enumeration
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S
from .uml import props

STEREOTYPES = [
    S("Guardrail", "guardrail", MC.CONSTRAINT, "guardrails", "Guardrail"),
    S("OutputContract", "output_contract", MC.CONSTRAINT, "output_contracts",
      "OutputContract"),
    S("Evaluation", "evaluation", MC.CONSTRAINT, "lifecycle.evaluations",
      "EvaluationCase"),
]


def _to_channel(owner: str, field: str, stereotype: str) -> R:
    return R(owner, "channel", K.ASSOCIATION, stereotype, field, SH.REF,
             target_mult="0..1", linkable=False, draw=Draw.NONE)


def _redacts(owner: str) -> R:
    return R(owner, "data_class", K.ASSOCIATION, "redacts",
             "redact_data_classes", SH.REFS, linkable=False, draw=Draw.NONE)


RELATIONSHIPS = [
    R("agent", "guardrail", K.ASSOCIATION, "guarded by", "guardrails", SH.REFS),
    R("worker", "output_contract", K.ASSOCIATION, "returns", "output_contract",
      SH.REF, target_mult="0..1"),
    R("guardrail", "data_class", K.ASSOCIATION, "protects", "data_classes",
      SH.REFS),
    R("evaluation", "agent", K.ASSOCIATION, "evaluates", "applies_to", SH.REFS),
    _to_channel("guardrail", "escalate_channel", "escalates on"),
    _to_channel("Budget", "notify_channel", "notifies"),
    _to_channel("Resilience", "dead_letter_channel", "dead letters to"),
    _redacts("Compliance"),
    _redacts("Observability"),
    R("Budget", "resource", K.ASSOCIATION, "caps", "scope", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind given by scope_kind; empty when scope_kind = "
                 "system}"),
]

ENUMERATIONS = [
    Enumeration("GuardrailKind", "GuardrailKind",
                ("input", "output", "tool_input", "tool_output"),
                "Where a guardrail sits in the loop (ADR-0035)."),
    Enumeration("GuardrailCheck", "GuardrailCheck",
                ("pii", "secrets", "prompt_injection", "data_class",
                 "url_allowlist", "schema", "pattern", "max_length"),
                "Named checks, so a guardrail is reviewable."),
    Enumeration("GuardrailAction", "GuardrailAction",
                ("block", "redact", "flag", "escalate"),
                "What happens when a check trips."),
    Enumeration("OutputViolationAction", "OutputViolationAction",
                ("retry", "block", "flag"),
                "What happens when an output breaks its contract "
                "(ADR-0037)."),
    Enumeration("LifecycleStage", "LifecycleStage",
                ("draft", "development", "staging", "production", "retired"),
                "Where a design is in its release life (ADR-0022)."),
    Enumeration("GateRequirement", "GateRequirement",
                ("evaluations_passed", "human_approval", "security_review",
                 "cost_within_budget", "owner_assigned",
                 "permissions_reviewed"),
                "What must be true before an agent advances a stage "
                "(ADR-0022)."),
    Enumeration("BreachAction", "BreachAction", ("warn", "throttle", "halt"),
                "What a budget does when it is exceeded."),
    Enumeration("BudgetScopeKind", "", ("system", "team", "agent"),
                "What a budget caps."),
    Enumeration("BudgetPeriod", "", ("daily", "weekly", "monthly"),
                "The period a budget resets over."),
    Enumeration("AlertSeverity", "", ("info", "warning", "error", "critical"),
                "How loudly an alert is raised."),
]

DATATYPES = [
    DataType("Lifecycle", "Lifecycle",
             "Stages, gates and the review cadence for the whole system "
             "(ADR-0022); its evaluations are Evaluation elements."),
    DataType("PromotionGate", "PromotionGate",
             "What must hold before an agent enters a stage (ADR-0022)."),
    DataType("Compliance", "Compliance",
             "Obligations that constrain placement, retention and "
             "disclosure."),
    DataType("Observability", "Observability",
             "The contract every target must satisfy (ADR-0016)."),
    DataType("AlertCondition", "AlertCondition",
             "A condition the observability sink raises an alert on."),
    DataType("Resilience", "Resilience",
             "Durability properties a target must provide (ADR-0025)."),
    DataType("Budget", "Budget",
             "A spend ceiling with a mandatory action on breach "
             "(ADR-0022)."),
    DataType("ModelPolicy", "ModelPolicy",
             "Which models an agent is permitted to run on (ADR-0040)."),
]

PROPERTIES = [
    *props("guardrail",
           "id: String", "description: String",
           "applies_to: GuardrailKind [0..*]",
           "checks: GuardrailCheck [0..*]",
           "on_violation: GuardrailAction", "patterns: String [0..*]",
           "allowed_urls: String [0..*]", "max_length: Integer [0..1]",
           "enabled: Boolean", "classifier: ModelClass [0..1]"),
    *props("output_contract",
           "id: String", "description: String",
           "schema_: Map -- a JSON Schema, written `schema`",
           "required: String [0..*]",
           "on_violation: OutputViolationAction", "max_retries: Integer"),
    *props("evaluation",
           "id: String", "description: String", "given: String",
           "expect: String", "must_not: String [0..*]", "weight: Real"),
    *props("Lifecycle",
           "stage: LifecycleStage", "owner: String -- a contact, in words",
           "gates: PromotionGate [0..*]", "review_cadence_days: Integer",
           "retire_after_idle_days: Integer [0..1]"),
    *props("PromotionGate",
           "to_stage: LifecycleStage", "requires: GateRequirement [0..*]",
           "min_pass_rate: Real",
           "approvers: String [0..*] -- who signs, by contact"),
    *props("Compliance",
           "frameworks: String [0..*]", "data_residency: String [0..*]",
           "audit_retention_days: Integer",
           "require_approval_for: Action [0..*]",
           "permission_review_days: Integer"),
    *props("Observability",
           "traces: Boolean", "metrics: Boolean", "logs: Boolean",
           "propagate_across_delegation: Boolean",
           "retention_days: Integer", "sample_rate: Real",
           "alerts: AlertCondition [0..*]"),
    *props("AlertCondition",
           "id: String", "description: String", "expression: String",
           "severity: AlertSeverity"),
    *props("Resilience",
           "checkpoint_each_step: Boolean", "resume_on_failure: Boolean",
           "idempotent_triggers: Boolean", "max_run_seconds: Integer"),
    *props("Budget",
           "id: String", "scope_kind: BudgetScopeKind",
           "period: BudgetPeriod", "limit_usd: Real",
           "on_breach: BreachAction"),
    *props("ModelPolicy",
           "classes: ModelClass [0..*]", "allow: String [0..*] -- model ids",
           "deny: String [0..*] -- model ids",
           "max_cost_per_million_tokens: Real [0..1]",
           "min_context_tokens: Integer [0..1]",
           "require_no_training_on_data: Boolean",
           "require_regions: String [0..*]",
           "subagent_classes: ModelClass [0..*]", "allow_fallback: Boolean"),
    *props("system",
           "model_policy: ModelPolicy", "budgets: Budget [0..*]",
           "compliance: Compliance", "resilience: Resilience",
           "observability: Observability"),
    *props("agent", "model_policy: ModelPolicy [0..1]"),
]

PROFILE = Profile(
    name="Assurance", imports=("Core", "Organisation", "Data", "Process"),
    doc="Guardrails, contracts, evaluations and the release, cost and "
        "operational policies.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    enumerations=ENUMERATIONS, datatypes=DATATYPES, properties=PROPERTIES,
)
