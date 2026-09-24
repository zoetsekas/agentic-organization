"""The Core profile (ADR-0112): the Model, comments, and what every other
profile shares — the abstract ends a policy is about, and the shared
enumerations and value types. Imports nothing but UML."""
from __future__ import annotations

from .uml import DataType, Enumeration
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Stereotype as S
from .uml import props

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
SYSTEM_OWNED = {"organization", "lifecycle.evaluations", "artifact_stores"}

ENUMERATIONS = [
    Enumeration("SharingScope", "SharingScope",
                ("private", "protected", "public"),
                "How widely something may travel: its owner only, named "
                "groups, or everyone in the organisation."),
    Enumeration("Action", "Action",
                ("read", "write", "query", "invoke", "delegate", "publish",
                 "approve", "administer"),
                "What may be done to a resource: the verb of a capability, a "
                "permission or a policy."),
    Enumeration("Effect", "Effect", ("allow", "deny"),
                "A policy's verdict. Deny always wins."),
    Enumeration("ModelClass", "ModelClass",
                ("frontier_reasoning", "balanced", "fast_cheap",
                 "long_context", "vision", "code", "embedding",
                 "on_premises"),
                "What an agent needs from a model, not which model it gets "
                "(ADR-0040)."),
    Enumeration("ChannelClass", "ChannelClass",
                ("direct", "async_bus", "team_chat", "mail", "webhook"),
                "Abstract communication surfaces; the binding picks the "
                "product."),
    Enumeration("SpecEnvironment", "",
                ("development", "staging", "production"),
                "Which environment a design is written for; rules tighten "
                "towards production."),
]

DATATYPES = [
    DataType("Metadata", "Metadata",
             "The design's name, versions, owner and labels."),
    DataType("DeploymentSpec", "DeploymentSpec",
             "Which targets and regions the design compiles for, and where "
             "its binding is. The binding itself is the Deployment "
             "profile's."),
]

PROPERTIES = [
    *props("system", "metadata: Metadata", "deployment: DeploymentSpec"),
    *props("Metadata",
           "name: String", "spec_version: String", "version: String",
           "description: String", "owner: String -- a contact, in words",
           "environment: SpecEnvironment", "labels: Map"),
    *props("DeploymentSpec",
           "targets: String [0..*] -- target names, resolved by the compiler",
           "regions: String [0..*]", "high_availability: Boolean",
           "binding: String [0..1] -- the path of the binding document"),
]

PROFILE = Profile(
    name="Core", imports=(),
    doc="The Model and what every concern shares.",
    stereotypes=STEREOTYPES, enumerations=ENUMERATIONS, datatypes=DATATYPES,
    properties=PROPERTIES,
)
