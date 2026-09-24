"""The Knowledge & tools profile (ADR-0112): what an agent knows and carries —
grounding knowledge, memory, skills, plugins and tools."""
from __future__ import annotations

from .uml import Draw
from .uml import MetaClass as MC
from .uml import Profile
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S

STEREOTYPES = [
    S("Skill", "skill", MC.ARTIFACT, "skills", "SkillSpec"),
    S("Plugin", "plugin", MC.COMPONENT, "plugins", "PluginSpec"),
    S("Tool", "tool", MC.INTERFACE, "tools", "ToolSpec"),
    S("Knowledge", "knowledge", MC.ARTIFACT, "knowledge", "KnowledgeSource"),
    S("MemoryNamespace", "memory_namespace", MC.ARTIFACT, "memory.namespaces",
      "MemoryNamespace"),
]

RELATIONSHIPS = [
    R("worker", "knowledge", K.ASSOCIATION, "consults", "knowledge", SH.REFS,
      help="grounding material this worker may consult"),
    R("agent", "skill", K.ASSOCIATION, "holds", "skills", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a named competence this agent may exercise. Shared freely: "
           "declaring it on a second agent does not take it from the first"),
    R("agent", "plugin", K.ASSOCIATION, "holds", "plugins", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a packaged extension this agent loads"),
    R("worker", "tool", K.USAGE, "holds", "tools", SH.REFS,
      draw=Draw.INLINE, legacy="holds",
      help="a tool this agent may call. A tool held by one agent is drawn "
           "inside it; one held by several is drawn shared"),
    R("knowledge", "data_class", K.ASSOCIATION, "contains", "data_classes",
      SH.REFS),
    R("memory_namespace", "data_class", K.ASSOCIATION, "stores",
      "data_classes", SH.REFS),
    R("skill", "capability", K.USAGE, "requires",
      "requires_capabilities", SH.REFS),
    R("plugin", "capability", K.USAGE, "requires",
      "requires_capabilities", SH.REFS),
    R("plugin", "skill", K.ASSOCIATION, "provides", "provides_skills", SH.REFS),
    R("plugin", "tool", K.ASSOCIATION, "provides", "provides_tools", SH.REFS),
    R("tool", "wrappable", K.ASSOCIATION, "wraps", "wraps", SH.REF,
      target_mult="1", linkable=False, draw=Draw.NONE,
      constraint="{kind given by wraps_kind}",
      help="what the tool names and narrows (ADR-0029)"),
    R("tool", "callable", K.REALIZATION, "", "", SH.REF, linkable=False,
      draw=Draw.NONE),
]

PROFILE = Profile(
    name="Knowledge", imports=("Core", "Organisation", "Data"),
    doc="What an agent knows and carries: knowledge, memory, skills, "
        "plugins and tools.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
)
