"""What the canvas can place, and what may be linked to what (ADR-0034)."""
from __future__ import annotations

from typing import Any

# What the canvas can place, and the form each component needs (ADR-0034).
# Derived from the spec model so the palette cannot drift from what validates.
# Which components may be linked to which, and what the spec calls it
# (ADR-0006, ADR-0024). The canvas asks rather than guesses: a drop used to
# nest whatever you dropped near whatever was nearest, which wrote a parent
# into the spec nobody asked for, and `nearestNode` had no distance limit so
# "near" meant "anywhere".
#
# Every rule names the spec field it writes, because a link a reader cannot
# trace to a field is a link the compiler will not see.
# What may be linked to what, derived from the UML profile (ADR-0101)
# rather than written by hand here. The nine rules this table used to hold
# are all still produced, under the same words, so every canvas code path
# that knew them still does; the rest of what the spec can hold is now
# drawable too.
from ..metamodel import link_rules as _link_rules
from ..spec.model import (
    POLICY_CONDITION_KEYS,
    Action,
    Effect,
    MissionStatus,
    ResourceKind,
)

LINK_RULES: list[dict[str, Any]] = _link_rules()


PALETTE: dict[str, Any] = {
    "groups": [
        {
            "id": "organization",
            "label": "Organization",
            "kinds": [
                {"kind": "team", "label": "Team", "icon": "▣",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "leader", "type": "string",
                      "help": "agent id; must also be a member"},
                     {"name": "description", "type": "text",
                      "help": "the unit's charter, in prose"},
                     {"name": "mandate", "type": "decisions",
                      "help": "what this unit may decide; empty inherits its "
                              "parent's, never everything"},
                     {"name": "groups", "type": "list"},
                     {"name": "placement", "type": "bool",
                      "help": "make this unit a placement boundary: its own "
                              "sandbox environment, shared volume and network "
                              "policy. Off means it sits in the nearest "
                              "ancestor that is one — and if nothing is, the "
                              "whole organisation shares one place"},
                 ]},
                {"kind": "skill", "label": "Skill", "icon": "◇",
                 "help": "instructions plus resources. Changes how an agent "
                         "works, never what it may reach",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "version", "type": "string"},
                     {"name": "instructions", "type": "text", "required": True},
                     {"name": "triggers", "type": "list",
                      "help": "phrases that invoke it"},
                     {"name": "requires_capabilities", "type": "list",
                      "help": "assumed, and checked against the holder's "
                              "grants — declaring one grants nothing"},
                     {"name": "resources", "type": "map",
                      "help": "files shipped with the skill: path = content"},
                 ]},
                {"kind": "plugin", "label": "Plugin", "icon": "❖",
                 "help": "a bundle of skills and tools",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "version", "type": "string"},
                     {"name": "provides_skills", "type": "list"},
                     {"name": "provides_tools", "type": "list"},
                     {"name": "requires_capabilities", "type": "list"},
                     {"name": "hooks", "type": "map",
                      "help": "event = handler, resolved by the target"},
                 ]},
                {"kind": "tool", "label": "Tool", "icon": "⚙",
                 "help": "a thin wrapper that narrows and names something "
                         "already granted; it grants nothing itself",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "wraps_kind", "type": "enum", "required": True,
                      "options": ["capability", "subagent", "workflow",
                                  "endpoint"]},
                     {"name": "wraps", "type": "string", "required": True},
                     {"name": "input_schema", "type": "json"},
                     {"name": "output_schema", "type": "json"},
                     {"name": "idempotent", "type": "bool"},
                 ]},
                {"kind": "guardrail", "label": "Guardrail", "icon": "⛨",
                 "help": "permissions decide what an agent may reach; a "
                         "guardrail decides what may pass",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "applies_to", "type": "multi", "required": True,
                      "options": ["input", "output", "tool_input",
                                  "tool_output"],
                      "help": "which of the four boundaries"},
                     {"name": "checks", "type": "multi", "required": True,
                      "options": ["pii", "secrets", "prompt_injection",
                                  "data_class", "url_allowlist", "schema",
                                  "pattern", "max_length"]},
                     {"name": "on_violation", "type": "enum",
                      "options": ["block", "redact", "flag", "escalate"]},
                     {"name": "data_classes", "type": "list",
                      "help": "for the data_class check"},
                     {"name": "patterns", "type": "list",
                      "help": "for the pattern check"},
                     {"name": "allowed_urls", "type": "list",
                      "help": "for the url_allowlist check"},
                     {"name": "max_length", "type": "number"},
                     {"name": "escalate_channel", "type": "string",
                      "help": "required when on_violation is escalate"},
                     {"name": "classifier", "type": "enum",
                      "options": ["", "frontier_reasoning", "balanced",
                                  "fast_cheap"],
                      "help": "a model class for judgement checks; empty is "
                              "the deterministic pattern floor"},
                     {"name": "enabled", "type": "bool"},
                 ]},
                {"kind": "output_contract", "label": "Output contract",
                 "icon": "▤",
                 "help": "a checkable shape, not a sentence about one",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "schema", "type": "json",
                      "help": "a small JSON-Schema subset: type, properties, "
                              "required, items, enum"},
                     {"name": "required", "type": "list"},
                     {"name": "on_violation", "type": "enum",
                      "options": ["retry", "block", "flag"]},
                 ]},
                {"kind": "evaluation", "label": "Evaluation case", "icon": "✓",
                 "help": "what an agent must get right before it may be "
                         "promoted — and before it may run unattended",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "description", "type": "text"},
                     {"name": "given", "type": "text", "required": True,
                      "help": "the situation or prompt"},
                     {"name": "expect", "type": "text", "required": True,
                      "help": "what a correct answer must contain or do; "
                              "`contains: ...` is checkable, prose is not"},
                     {"name": "must_not", "type": "list"},
                     {"name": "applies_to", "type": "list",
                      "help": "which agents; empty applies to every one of "
                              "them, which is wider than most people mean"},
                     {"name": "weight", "type": "number"},
                 ]},
                {"kind": "separation", "label": "Separation of duties",
                 "icon": "⊘",
                 "help": "decisions no single agent may hold together",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     # A plain list of decision ids, not a mandate: a
                     # separation has no inherit-or-empty question to ask.
                     {"name": "decisions", "type": "decision_refs",
                      "required": True},
                     {"name": "reason", "type": "text",
                      "help": "a rule without one is a rule nobody defends"},
                 ]},
                {"kind": "policy", "label": "Policy rule", "icon": "⊙",
                 "help": "an explicit allow or deny on top of the roles. A "
                         "deny always wins and cannot be overridden",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "effect", "type": "enum",
                      "options": [e.value for e in Effect], "required": True},
                     {"name": "description", "type": "text",
                      "help": "shown in the refusal, so write the sentence "
                              "somebody refused should read"},
                     {"name": "actions", "type": "multi",
                      "options": [a.value for a in Action],
                      "help": "empty means every action"},
                     {"name": "resource_kinds", "type": "multi",
                      "options": [r.value for r in ResourceKind],
                      "help": "empty means every kind"},
                     {"name": "resources", "type": "list",
                      "help": "ids or globs within the kind; `*` is all"},
                     {"name": "subjects", "type": "list",
                      "help": "agent, team or role ids; `*` is everyone"},
                     # The keys come from the model, not from the UI: a form
                     # that offered its own list could offer one nothing
                     # evaluates, which is the defect these fields exist to
                     # make impossible.
                     {"name": "conditions", "type": "conditions",
                      "options": sorted(POLICY_CONDITION_KEYS),
                      "help": "all of these must hold for the rule to apply"},
                     {"name": "unless", "type": "conditions",
                      "options": sorted(POLICY_CONDITION_KEYS),
                      "help": "where this holds, the rule does not apply — "
                              "how 'deny everywhere except the clean room' is "
                              "written. A key nothing evaluates would switch "
                              "the whole rule off, so only these are offered"},
                 ]},
                {"kind": "person", "label": "Person", "icon": "☺",
                 "help": "a human principal: what they may decide, never "
                         "what they may reach. A person's access is their "
                         "employer's to mediate (ADR-0079)",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "contact", "type": "string",
                      "help": "two people sharing one is refused: one human "
                              "is one principal"},
                     {"name": "position", "type": "string",
                      "help": "their job title, as prose. Authority attaches "
                              "to the person, not the position, so it rots "
                              "when they move"},
                     {"name": "unit", "type": "string",
                      "help": "the org unit that bounds their authority; "
                              "empty sits them under the root"},
                     {"name": "mandate", "type": "decisions",
                      "help": "what they may decide. There is deliberately "
                              "no field here for what they may reach"},
                 ]},
                {"kind": "decision", "label": "Decision class", "icon": "§",
                 "help": "what a unit may decide, referenced by a mandate",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "title", "type": "string"},
                     {"name": "description", "type": "text"},
                 ]},
                {"kind": "agent", "label": "Agent", "icon": "◆",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "description", "type": "text",
                      "help": "what it is for — the blurb another agent reads "
                              "to decide when to delegate to it"},
                     {"name": "instructions", "type": "text",
                      "help": "how it operates — its system prompt, in your "
                              "words. The org context is added automatically; "
                              "this does not replace it (ADR-0083)"},
                     {"name": "roles", "type": "list"},
                     {"name": "capabilities", "type": "list"},
                     {"name": "knowledge", "type": "list"},
                     {"name": "skills", "type": "list"},
                     {"name": "plugins", "type": "list"},
                     {"name": "tools", "type": "list"},
                     {"name": "endpoints", "type": "list"},
                     {"name": "environments", "type": "list",
                      "help": "the sandbox class(es) it runs in; one or more "
                              "(ADR-0082)"},
                     {"name": "mandate", "type": "decisions",
                      "help": "what this agent may decide alone; empty "
                              "inherits its team's"},
                     {"name": "autonomy", "type": "autonomy",
                      "help": "per capability, and may only tighten what the "
                              "capability declares"},
                     {"name": "model_policy", "type": "object",
                      "help": "which models this agent may run on; says a "
                              "class, never a vendor",
                      "fields": [
                          {"name": "classes", "type": "multi",
                           "options": ["frontier_reasoning", "balanced",
                                       "fast_cheap"]},
                          {"name": "allow", "type": "list",
                           "help": "catalog entry ids, when an organization "
                                   "names models directly"},
                          {"name": "deny", "type": "list"},
                          {"name": "max_cost_per_million_tokens",
                           "type": "number"},
                          {"name": "min_context_tokens", "type": "number"},
                          {"name": "require_no_training_on_data",
                           "type": "bool"},
                          {"name": "require_regions", "type": "list"},
                          {"name": "subagent_classes", "type": "multi",
                           "options": ["frontier_reasoning", "balanced",
                                       "fast_cheap"]},
                          {"name": "allow_fallback", "type": "bool",
                           "help": "off by default: a fallback is a quiet "
                                   "change of model, so it is opted into"},
                      ]},
                     {"name": "shared_service", "type": "bool"},
                     {"name": "leads_team", "type": "bool",
                      "help": "whether this agent leads the team it is in. "
                              "Not a field on the agent: it sets the team's "
                              "leader, so ticking it here hands leadership "
                              "over from whoever held it"},
                     {"name": "workflows", "type": "list",
                      "help": "encoded processes this agent may invoke; a "
                              "trigger can only run one the agent holds"},
                     {"name": "produces_data", "type": "list",
                      "help": "data classes this agent produces, so a "
                              "dependency on one has a named other party "
                              "rather than an assumption (ADR-0099)"},
                     {"name": "data_dependencies", "type": "json",
                      "help": "what this agent *relies on*, as distinct from "
                              "what it may touch: {data_class, fields, "
                              "max_age_seconds, on_stale, produced_by}. "
                              "A grant says it may read; this says what it is "
                              "counting on, and what to do when that does not "
                              "hold"},
                     {"name": "successor", "type": "string",
                      "blank": "— its manager stands in —",
                      "help": "who stands in when this agent cannot run. "
                              "Leave unset and its manager does — already "
                              "holding this mandate, so nothing is granted. "
                              "Naming a peer lends authority the chart did "
                              "not, so it is bounded, recorded, and refused "
                              "if it would collapse a separation (ADR-0094)"},
                     {"name": "scaling", "type": "object",
                      "help": "how many of this agent run. Leave unset and "
                              "the platform default is still emitted "
                              "explicitly — the one thing ruled out is a "
                              "ceiling nobody chose (ADR-0095)",
                      "fields": [
                          {"name": "min_instances", "type": "number",
                           "help": "kept warm. 0 scales to zero, which drops "
                                   "any asynchronous work this agent was "
                                   "holding — they settle as failed, not "
                                   "silently"},
                          {"name": "max_instances", "type": "number",
                           "help": "the ceiling. Times the per-instance "
                                   "figure below, this is how much work can "
                                   "be in flight — a different bound from "
                                   "max_parallel_subagents"},
                          {"name": "concurrent_sessions_per_instance",
                           "type": "number"},
                      ]},
                     {"name": "humans", "type": "humans"},
                 ]},
                # A mission is a short-lived team drawn from the standing
                # organization (ADR-0039). It was missing here, so the canvas
                # could draw one and nobody could place one — the end date is
                # required because a mission that never ends is a
                # reorganization and belongs in the org chart.
                {"kind": "mission", "label": "Mission", "icon": "◍",
                 "help": "a short-lived team drawn from the standing "
                         "organisation. It always ends (ADR-0039)",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "objective", "type": "text", "required": True},
                     {"name": "deliverables", "type": "list"},
                     {"name": "success_criteria", "type": "list"},
                     {"name": "status", "type": "enum",
                      "options": [m.value for m in MissionStatus]},
                     {"name": "leader", "type": "string",
                      "help": "agent id; must also be a member"},
                     {"name": "members", "type": "list",
                      "help": "agent ids drawn from the standing organization. "
                              "They keep their home team and their own "
                              "permissions"},
                     {"name": "starts_on", "type": "string", "help": "ISO date"},
                     {"name": "ends_on", "type": "string", "required": True,
                      "help": "ISO date; a mission always ends"},
                     {"name": "internal_delegation", "type": "bool",
                      "help": "members may hand work to each other for the "
                              "mission's duration — declared, not assumed"},
                     {"name": "mandate", "type": "decisions",
                      "help": "authority lent for the window, bounded by the "
                              "line it is drawn from and expiring with it "
                              "(ADR-0065 rule 8). Without it a mission is an "
                              "authority hole"},
                 ]},
                {"kind": "subagent", "label": "Sub-agent", "icon": "◇",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "parent", "type": "string", "required": True,
                      "help": "the agent that calls this sub-agent. Not a key "
                              "on the sub-agent — it is which agent's list "
                              "holds it, so changing it here moves it"},
                     {"name": "kind", "type": "enum",
                      "options": ["research", "review", "summarize", "extract",
                                  "critique", "plan", "verify", "custom"]},
                     {"name": "purpose", "type": "text"},
                     {"name": "capabilities", "type": "list"},
                     {"name": "returns", "type": "string"},
                     {"name": "max_runtime_seconds", "type": "number"},
                 ]},
                {"kind": "role", "label": "Role", "icon": "✦",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "kind", "type": "enum", "options": ["agent", "team"]},
                     {"name": "responsibilities", "type": "list"},
                     {"name": "capabilities", "type": "list"},
                 ]},
            ],
        },
        {
            "id": "access",
            "label": "Access",
            "kinds": [
                {"kind": "capability", "label": "Capability", "icon": "⚷",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "action", "type": "enum",
                      "options": ["read", "write", "query", "invoke", "delegate",
                                  "publish", "approve", "administer"]},
                     {"name": "resource_class", "type": "string"},
                     {"name": "data_classes", "type": "list"},
                     {"name": "secret_ref", "type": "string"},
                 ]},
                {"kind": "data_class", "label": "Data class", "icon": "▤",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "scope", "type": "enum",
                      "options": ["private", "protected", "public"]},
                     {"name": "groups", "type": "list"},
                     {"name": "may_leave_region", "type": "bool"},
                     {"name": "may_appear_in_traces", "type": "bool"},
                     {"name": "semantics", "type": "enum",
                      "options": ["unspecified", "subject", "event",
                                  "reference", "derived", "aggregate"],
                      "help": "what this data *is*, as distinct from how it "
                              "is protected (ADR-0099)"},
                     {"name": "relations", "type": "json",
                      "help": "how it relates to other classes: "
                              "{kind, target}. `derived_from` carries "
                              "restrictions along it, so a class derived from "
                              "data that may not leave its region may not say "
                              "that it may"},
                     {"name": "schema_ref", "type": "string",
                      "help": "where this class's schema lives — a URL or a "
                              "data-catalogue id. A pointer only: the platform "
                              "never reads, copies or checks it (ADR-0111)"},
                 ]},
                {"kind": "environment", "label": "Environment", "icon": "▦",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "tier", "type": "enum",
                      "options": ["minimal", "small", "medium", "large",
                                  "accelerated"]},
                     {"name": "network", "type": "enum",
                      "options": ["none", "allowlist", "internal", "open"]},
                     {"name": "mounts", "type": "list"},
                     {"name": "timeout_seconds", "type": "number"},
                 ]},
                {"kind": "endpoint", "label": "External agent", "icon": "⇥",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "trust", "type": "enum",
                      "options": ["internal", "partner", "external"]},
                     {"name": "send_data_classes", "type": "list"},
                     {"name": "requires_approval", "type": "bool"},
                 ]},
            ],
        },
        {
            "id": "operations",
            "label": "Operations",
            "kinds": [
                {"kind": "channel", "label": "Channel", "icon": "✉",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "channel_class", "type": "enum",
                      "options": ["direct", "async_bus", "team_chat", "mail",
                                  "webhook"]},
                     {"name": "human_facing", "type": "bool"},
                     {"name": "purposes", "type": "list"},
                     {"name": "response_sla_minutes", "type": "number"},
                 ]},
                {"kind": "trigger", "label": "Trigger", "icon": "⏱",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "kind", "type": "enum",
                      "options": ["schedule", "event", "webhook", "message",
                                  "manual"]},
                     {"name": "agent", "type": "string", "required": True},
                     {"name": "cadence", "type": "string",
                      "help": "cron or 'every 15 minutes'"},
                     {"name": "deliver_to", "type": "list"},
                 ]},
                {"kind": "knowledge", "label": "Knowledge", "icon": "▥",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "kind", "type": "enum",
                      "options": ["document_store", "wiki", "ticketing", "crm",
                                  "mailbox", "code_repository", "data_warehouse",
                                  "web"]},
                     {"name": "data_classes", "type": "list"},
                 ]},
                {"kind": "memory_namespace", "label": "Memory namespace",
                 "icon": "◈",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "scope", "type": "enum",
                      "options": ["private", "protected", "public"]},
                     {"name": "groups", "type": "list"},
                     {"name": "data_classes", "type": "list"},
                     {"name": "retention_days", "type": "number"},
                 ]},
                {"kind": "workflow", "label": "Workflow", "icon": "⤳",
                 "fields": [
                     {"name": "id", "type": "string", "required": True},
                     {"name": "name", "type": "string"},
                     {"name": "description", "type": "text"},
                     {"name": "graph", "type": "graph",
                      "help": "the process itself: steps, and what follows "
                              "what. A branch is the only step that may have "
                              "several ways out, because it is the only one "
                              "that chooses; a step nothing reaches is "
                              "refused, and a loop back to an earlier step is "
                              "allowed and bounded (ADR-0096)"},
                     {"name": "interrupt_before", "type": "list",
                      "help": "step ids to pause at for a person"},
                     {"name": "body", "type": "enum",
                      "options": ["graph", "external"],
                      "help": "graph: the steps are drawn here. external: "
                              "the insides are built in an engine the "
                              "binding names, and only the interface below "
                              "is held here (ADR-0110)"},
                     {"name": "interface", "type": "interface",
                      "help": "what goes in and out, what it calls, and the "
                              "data classes it receives and returns. A step "
                              "that calls this workflow is checked against "
                              "it: its owner must hold what it calls and the "
                              "data it is sent (ADR-0110)"},
                 ]},
                {"kind": "note", "label": "Note", "icon": "✎",
                 "fields": [{"name": "note", "type": "text"}]},
            ],
        },
    ],
}


# Infrastructure options the designer offers for each concern. These are
# declarative choices rendered as dropdowns; the deployment layer maps the
# selection onto the actual cluster resources.
INFRASTRUCTURE_CATALOG: dict[str, list[dict[str, str]]] = {
    "storage": [
        {"id": "postgres", "name": "PostgreSQL", "use": "Org, agents, sessions, catalog"},
        {"id": "s3", "name": "Object storage", "use": "Session artifacts, sandbox outputs"},
        {"id": "pgvector", "name": "Vector index", "use": "Public/protected knowledge search"},
        {"id": "redis", "name": "Redis", "use": "Session cache, rate limits, locks"},
    ],
    "compute": [
        {"id": "k8s_jobs", "name": "Kubernetes jobs", "use": "Sandbox execution"},
        {"id": "firecracker", "name": "microVM pool", "use": "Untrusted code isolation"},
        {"id": "gpu_pool", "name": "GPU pool", "use": "ml-training sandbox template"},
        {"id": "serverless", "name": "Serverless functions", "use": "Short tool calls"},
    ],
    "communication": [
        {"id": "nats", "name": "NATS / Kafka", "use": "Internal agent message bus"},
        {"id": "slack", "name": "Slack", "use": "Human-facing channels"},
        {"id": "teams", "name": "Microsoft Teams", "use": "Human-facing channels"},
        {"id": "smtp", "name": "SMTP relay", "use": "Email escalation"},
    ],
    "operations": [
        {"id": "otel", "name": "OpenTelemetry", "use": "Session traces and spans"},
        {"id": "loki", "name": "Log aggregation", "use": "Structured agent logs"},
        {"id": "prometheus", "name": "Prometheus", "use": "Metrics and SLOs"},
        {"id": "pagerduty", "name": "PagerDuty", "use": "Alert routing to humans"},
    ],
    "security": [
        {"id": "vault", "name": "Secret manager", "use": "DSNs, API tokens, MCP secrets"},
        {"id": "oidc", "name": "OIDC / SSO", "use": "Human identity on sessions"},
        {"id": "egress_proxy", "name": "Egress proxy", "use": "Sandbox network allowlists"},
        {"id": "audit_log", "name": "Immutable audit log", "use": "Tool calls and approvals"},
    ],
}


# How the palette reads, and what nests under what (ADR-0034).
#
# The groups above were the order things were added in: Team, then skills and
# plugins, then guardrails, then a person, then finally Agent — fifteen kinds
# in one list with no shape. A palette is the first thing somebody meets, and
# a jumble teaches nothing about the model.
#
# The nesting is not decoration: a child is a component the parent *contains*
# in the spec. A Tool nested under Agent says `agent.tools`, the same fact
# `LINK_RULES` states for linking. Where both speak they agree, and a test
# holds them to it.
PALETTE_TREE: list[dict[str, Any]] = [
    {
        "id": "organisation",
        "label": "Organisation",
        "help": "the standing structure: who exists and who they answer to",
        "kinds": [
            {"kind": "team", "children": [
                {"kind": "agent", "children": [
                    {"kind": "subagent"},
                    {"kind": "skill"},
                    {"kind": "plugin"},
                    {"kind": "tool"},
                ]},
            ]},
            # Declared once at the top level and referenced by the agents they
            # are paired with, so a person is not nested under one (ADR-0079).
            {"kind": "person"},
            {"kind": "role"},
        ],
    },
    {
        "id": "authority",
        "label": "Authority",
        "help": "what may be decided, and what may not be decided together",
        "kinds": [
            {"kind": "decision"},
            {"kind": "separation"},
            {"kind": "policy"},
        ],
    },
    {
        "id": "access",
        "label": "Access",
        "help": "what may be reached, and from where",
        "kinds": [
            {"kind": "capability"},
            {"kind": "data_class"},
            {"kind": "environment"},
            {"kind": "endpoint"},
        ],
    },
    {
        "id": "work",
        "label": "Work",
        "help": "what actually happens, and what wakes it",
        "kinds": [
            {"kind": "mission"},
            {"kind": "workflow"},
            {"kind": "trigger"},
            {"kind": "channel"},
            {"kind": "knowledge"},
            {"kind": "memory_namespace"},
        ],
    },
    {
        "id": "assurance",
        "label": "Assurance",
        "help": "what must hold before this runs unattended",
        "kinds": [
            {"kind": "guardrail"},
            {"kind": "output_contract"},
            {"kind": "evaluation"},
        ],
    },
    {
        "id": "canvas",
        "label": "Canvas",
        "help": "annotation; nothing here reaches the spec",
        "kinds": [{"kind": "note"}],
    },
]


def _palette_definitions() -> dict[str, dict[str, Any]]:
    """Every kind's fields, by kind, from the flat groups above."""
    return {k["kind"]: k for group in PALETTE["groups"] for k in group["kinds"]}


def _compose(node: dict[str, Any],
             defs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    definition = defs.get(node["kind"])
    if definition is None:                      # a tree entry with no fields
        raise KeyError(f"the palette tree names '{node['kind']}', "
                       "which no group defines")
    composed = dict(definition)
    if node.get("children"):
        composed["children"] = [_compose(c, defs) for c in node["children"]]
    return composed


def palette_tree() -> list[dict[str, Any]]:
    """The palette as a tree, in a deliberate order."""
    defs = _palette_definitions()
    return [
        {**group, "kinds": [_compose(k, defs) for k in group["kinds"]]}
        for group in PALETTE_TREE
    ]


def palette_kinds(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every kind in a palette, nested ones included."""
    out: list[dict[str, Any]] = []

    def walk(kinds: list[dict[str, Any]]) -> None:
        for kind in kinds:
            out.append(kind)
            walk(kind.get("children") or [])

    for group in groups:
        walk(group["kinds"])
    return out
