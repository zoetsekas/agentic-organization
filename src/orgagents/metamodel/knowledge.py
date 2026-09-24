"""The Knowledge & tools profile (ADR-0112): what an agent knows and carries —
grounding knowledge, memory and its policies, skills, plugins, tools, and how
a long run keeps its context usable."""
from __future__ import annotations

from .uml import DataType, Draw, Enumeration, Profile, props
from .uml import MetaClass as MC
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
    # -- memory and context (ADR-0028, ADR-0036) ------------------------------
    R("MemoryPolicy", "data_class", K.ASSOCIATION, "may hold",
      "data_classes", SH.REFS, linkable=False, draw=Draw.NONE),
    R("MemoryPolicy", "data_class", K.ASSOCIATION, "redacts",
      "redact_data_classes", SH.REFS, linkable=False, draw=Draw.NONE),
    R("AgentMemoryOverride", "memory_namespace", K.ASSOCIATION, "may use",
      "namespaces", SH.REFS, linkable=False, draw=Draw.NONE),
    R("ContextPolicy", "artifact_store", K.ASSOCIATION, "offloads to",
      "offload_to", SH.REF, target_mult="0..1", linkable=False,
      draw=Draw.NONE),
]

ENUMERATIONS = [
    Enumeration("KnowledgeKind", "KnowledgeKind",
                ("document_store", "wiki", "ticketing", "crm", "mailbox",
                 "code_repository", "data_warehouse", "web"),
                "Classes of grounding source, not products (ADR-0023)."),
    Enumeration("MemoryTier", "MemoryTier", ("session", "long_term"),
                "Where a memory lives (ADR-0028)."),
    Enumeration("RecallMode", "RecallMode",
                ("none", "on_demand", "automatic"),
                "How remembered items come back into a run."),
    Enumeration("WrapsKind", "",
                ("capability", "subagent", "workflow", "endpoint"),
                "What kind of «Wrappable» a tool wraps."),
]

DATATYPES = [
    DataType("Memory", "Memory",
             "The two-tier memory contract for the system (ADR-0028); its "
             "namespaces are MemoryNamespace elements."),
    DataType("MemoryPolicy", "MemoryPolicy",
             "How one memory tier behaves (ADR-0028)."),
    DataType("AgentMemoryOverride", "AgentMemoryOverride",
             "An agent's narrowing of the system memory contract."),
    DataType("ContextPolicy", "ContextPolicy",
             "How a long run keeps its context usable and affordable "
             "(ADR-0036)."),
]

PROPERTIES = [
    *props("knowledge",
           "id: String", "description: String", "kind: KnowledgeKind",
           "freshness_seconds: Integer [0..1]", "require_citation: Boolean",
           "secret_ref: String [0..1]"),
    *props("memory_namespace",
           "id: String", "description: String", "scope: SharingScope",
           "groups: String [0..*]", "retention_days: Integer [0..1]"),
    *props("skill",
           "id: String", "description: String", "version: String",
           "instructions: String",
           "triggers: String [0..*] -- phrases that call for it",
           "resources: Map -- named files the skill ships"),
    *props("plugin",
           "id: String", "description: String", "version: String",
           "hooks: Map"),
    *props("tool",
           "id: String", "description: String", "wraps_kind: WrapsKind",
           "input_schema: Map", "output_schema: Map",
           "constraints: CapabilityConstraint -- may narrow what it wraps, "
           "never widen it",
           "idempotent: Boolean", "validate_output: Boolean"),
    *props("Memory",
           "session: MemoryPolicy", "long_term: MemoryPolicy"),
    *props("MemoryPolicy",
           "tier: MemoryTier", "enabled: Boolean",
           "retention_days: Integer [0..1]", "max_items: Integer",
           "recall: RecallMode", "promotion_allowed: Boolean",
           "promotion_requires_approval: Boolean"),
    *props("AgentMemoryOverride",
           "session_retention_minutes: Integer [0..1]",
           "long_term_enabled: Boolean", "recall: RecallMode [0..1]",
           "may_promote: Boolean"),
    *props("ContextPolicy",
           "max_context_tokens: Integer", "summarize_after_tokens: Integer",
           "keep_last_turns: Integer", "offload_tool_output_bytes: Integer",
           "retain_summaries: Boolean",
           "summarizer: ModelClass [0..1]"),
    *props("system", "context: ContextPolicy"),
    *props("agent", "memory: AgentMemoryOverride [0..1]",
           "context: ContextPolicy [0..1]"),
]

PROFILE = Profile(
    name="Knowledge", imports=("Core", "Organisation", "Data"),
    doc="What an agent knows and carries: knowledge, memory, skills, "
        "plugins and tools.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    enumerations=ENUMERATIONS, datatypes=DATATYPES, properties=PROPERTIES,
)
