"""The Data profile (ADR-0112, ADR-0111): the conceptual data model — data
classes, how they relate, who produces and relies on them, what reaches them
and where agents keep what they write."""
from __future__ import annotations

from .uml import Draw, Enumeration
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S
from .uml import props

STEREOTYPES = [
    S("DataClass", "data_class", MC.DATA_TYPE, "data_classes", "DataClass"),
    S("ArtifactStore", "artifact_store", MC.CLASS, "artifact_stores",
      "ArtifactStore",
      "A workspace agents read and write files in (ADR-0036). Owned by the "
      "System: where work is kept, not what the organisation is.",
      palette=False),
]


def _relation(kind: K, stereotype: str, literal: str, help_: str,
              constraint: str = "") -> R:
    """One of ADR-0111's data-class relations: the objects of
    `data_class.relations` whose `kind` is `literal`."""
    return R("data_class", "data_class", kind, stereotype, "relations",
             SH.REF_OBJECTS, key="target", selector=("kind", literal),
             association_class="DataRelation", linkable=False,
             draw=Draw.NONE, constraint=constraint, help=help_)


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

    # -- data classes relate to each other (ADR-0111) ------------------------
    _relation(K.ABSTRACTION, "derive", "derived_from",
              "the source is computed from the target; restrictions flow "
              "along it (ADR-0099)",
              "{a restriction of the target holds for the source}"),
    _relation(K.COMPOSITION, "part of", "part_of",
              "the source is a component of the target",
              "{the source is the part, the target the whole}"),
    _relation(K.ASSOCIATION, "identifies", "identifies",
              "the source names the subject the target is about"),
    _relation(K.ASSOCIATION, "references", "references",
              "a plain pointer; carries no restriction"),

    R("data_class", "environment", K.ASSOCIATION, "may be processed in",
      "allowed_environments", SH.REFS, linkable=False, draw=Draw.NONE,
      constraint="{empty = any environment}"),
    R("DataDependency", "agent", K.ASSOCIATION, "produced by", "produced_by",
      SH.REF, target_mult="0..1", linkable=False, draw=Draw.NONE,
      help="the agent the reader expects to produce it"),
    R("agent", "artifact_store", K.ASSOCIATION, "writes to",
      "artifact_store", SH.REF, target_mult="0..1", linkable=False,
      draw=Draw.NONE, help="the workspace large results go to (ADR-0036)"),
    R("artifact_store", "data_class", K.ASSOCIATION, "may hold",
      "data_classes", SH.REFS, linkable=False, draw=Draw.NONE),
]

ENUMERATIONS = [
    Enumeration("DataSemantics", "DataSemantics",
                ("unspecified", "subject", "event", "reference", "derived",
                 "aggregate"),
                "What a class of data *is*, as distinct from how it is "
                "protected; shown as a stereotype label on the Data diagram."),
    Enumeration("DataRelationKind", "DataRelationKind",
                ("derived_from", "identifies", "part_of", "references"),
                "How one class of data relates to another. Each literal is "
                "one of the profile's relationships (ADR-0111).",
                uml=(("derived_from", "Abstraction «derive»"),
                     ("identifies", "Association «identifies»"),
                     ("part_of", "Composition"),
                     ("references", "Association"))),
    Enumeration("StaleAction", "StaleAction",
                ("refuse", "degrade", "escalate"),
                "What to do when data is older than the agent said it could "
                "rely on (ADR-0099)."),
]

PROPERTIES = [
    *props("data_class",
           "id: String", "description: String",
           "scope: SharingScope", "groups: String [0..*] -- directory groups "
           "that may see it when protected",
           "may_leave_region: Boolean", "may_appear_in_traces: Boolean",
           "retention_days: Integer [0..1]", "semantics: DataSemantics"),
    *props("DataRelation",
           "kind: DataRelationKind -- which of the four relationships the "
           "link is", "description: String"),
    *props("DataDependency",
           "fields: String [0..*] -- the fields relied on",
           "max_age_seconds: Integer [0..1] -- the freshness window",
           "on_stale: StaleAction", "description: String"),
    *props("artifact_store",
           "id: String", "description: String", "scope: SharingScope",
           "groups: String [0..*]", "retention_days: Integer [0..1]",
           "max_file_bytes: Integer", "max_total_bytes: Integer"),
]

PROFILE = Profile(
    name="Data", imports=("Core", "Organisation", "Access"),
    doc="The conceptual data model: classes, their relations, producers "
        "and consumers.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    enumerations=ENUMERATIONS, properties=PROPERTIES,
)
