"""The Core profile (ADR-0112): the Model, comments, and what every other
profile shares — the abstract ends a policy is about, and the shared
enumerations and value types. Imports nothing but UML."""
from __future__ import annotations

from .uml import MetaClass as MC
from .uml import Profile
from .uml import Stereotype as S

STEREOTYPES = [
    S("Principal", "principal", MC.INTERFACE, "", "",
      "Anything a policy can be about: an agent, a team, a role.",
      abstract=True, palette=False),
    S("Resource", "resource", MC.INTERFACE, "", "",
      "Anything a permission or policy can govern (ADR-0008).",
      abstract=True, palette=False),
    S("Note", "note", MC.COMMENT, "", ""),
    S("System", "system", MC.MODEL, "", "SystemSpec",
      "The Model: one organisation design, owning every element in it.",
      palette=False),
]

#: Collections that stay on the System: how the design is released, not what
#: the organisation is (ADR-0101).
SYSTEM_OWNED = {"organization", "lifecycle.evaluations"}

PROFILE = Profile(
    name="Core", imports=(),
    doc="The Model and what every concern shares.",
    stereotypes=STEREOTYPES,
)
