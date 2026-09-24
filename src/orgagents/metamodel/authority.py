"""The Authority profile (ADR-0112): what may be decided and by whom —
decisions, separations of duty and policies."""
from __future__ import annotations

from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S

STEREOTYPES = [
    S("Decision", "decision", MC.DATA_TYPE, "decisions", "DecisionClass"),
    S("Separation", "separation", MC.CONSTRAINT, "separations",
      "SeparationRule"),
    S("Policy", "policy", MC.CONSTRAINT, "policies", "PolicyRule"),
]

RELATIONSHIPS = [
    R("separation", "decision", K.ASSOCIATION, "keeps apart", "decisions",
      SH.REFS, target_mult="2..*"),
    R("policy", "principal", K.ASSOCIATION, "applies to", "subjects", SH.REFS,
      constraint="{'*' = every principal}",
      help="who the rule is about: agents, teams or roles, by id"),
    R("policy", "resource", K.ASSOCIATION, "governs", "resources", SH.REFS,
      constraint="{kind given by resource_kinds; '*' = every}",
      help="what the rule allows or denies acting on"),
]

PROFILE = Profile(
    name="Authority", imports=("Core", "Organisation"),
    doc="What may be decided, by whom, and what a rule allows or denies.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
)
