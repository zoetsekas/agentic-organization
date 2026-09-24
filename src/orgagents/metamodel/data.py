"""The Data profile (ADR-0112, ADR-0111): the conceptual data model — data
classes, how they relate, who produces and relies on them, and what reaches
them."""
from __future__ import annotations

from .uml import Draw
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S

STEREOTYPES = [
    S("DataClass", "data_class", MC.DATA_TYPE, "data_classes", "DataClass"),
]

RELATIONSHIPS = [
    R("agent", "data_class", K.ASSOCIATION, "produces", "produces_data",
      SH.REFS, help="data this agent produces (ADR-0099)"),
    R("agent", "data_class", K.ASSOCIATION, "relies on", "data_dependencies",
      SH.REF_OBJECTS, key="data_class", association_class="DataDependency",
      help="data this agent relies on, with freshness (ADR-0099)"),
    R("capability", "data_class", K.ASSOCIATION, "reaches", "data_classes",
      SH.REFS),
    R("endpoint", "data_class", K.ASSOCIATION, "may receive",
      "send_data_classes", SH.REFS),
    R("data_class", "resource", K.REALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE),
]

PROFILE = Profile(
    name="Data", imports=("Core", "Organisation", "Access"),
    doc="The conceptual data model: classes, their relations, producers "
        "and consumers.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
)
