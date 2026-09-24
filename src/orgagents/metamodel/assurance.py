"""The Assurance profile (ADR-0112): what keeps a design safe and releasable —
guardrails, output contracts, evaluations, and the release, cost and
operational policies."""
from __future__ import annotations

from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S

STEREOTYPES = [
    S("Guardrail", "guardrail", MC.CONSTRAINT, "guardrails", "Guardrail"),
    S("OutputContract", "output_contract", MC.CONSTRAINT, "output_contracts",
      "OutputContract"),
    S("Evaluation", "evaluation", MC.CONSTRAINT, "lifecycle.evaluations",
      "EvaluationCase"),
]

RELATIONSHIPS = [
    R("agent", "guardrail", K.ASSOCIATION, "guarded by", "guardrails", SH.REFS),
    R("worker", "output_contract", K.ASSOCIATION, "returns", "output_contract",
      SH.REF, target_mult="0..1"),
    R("guardrail", "data_class", K.ASSOCIATION, "protects", "data_classes",
      SH.REFS),
    R("evaluation", "agent", K.ASSOCIATION, "evaluates", "applies_to", SH.REFS),
]

PROFILE = Profile(
    name="Assurance", imports=("Core", "Organisation", "Data"),
    doc="Guardrails, contracts, evaluations and the release, cost and "
        "operational policies.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
)
