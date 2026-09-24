"""The Process profile (ADR-0112, ADR-0102, ADR-0110): how work happens —
workflows as UML Activities with their actions and control flows, the seam to
a body built in an engine, triggers and channels."""
from __future__ import annotations

from .uml import DataType, Draw, Enumeration, Profile, Property, props
from .uml import MetaClass as MC
from .uml import Relationship as R
from .uml import RelKind as K
from .uml import Shape as SH
from .uml import Stereotype as S

STEREOTYPES = [
    S("Action", "action", MC.ACTION, "", "ActivityNode",
      "A step of a workflow (ADR-0102).", palette=False),
    S("ControlFlow", "control_flow", MC.CONTROL_FLOW, "", "ControlFlow",
      "An edge between two steps of a workflow.", palette=False),
    S("StepOwner", "step_owner", MC.INTERFACE, "", "",
      "Whoever may own a workflow step: an agent, a team or a person "
      "(ADR-0110).", abstract=True, palette=False),
    S("Workflow", "workflow", MC.ACTIVITY, "workflows", "WorkflowSpec"),
    S("Trigger", "trigger", MC.EVENT, "triggers", "TriggerSpec"),
    S("Channel", "channel", MC.CLASS, "channels", "ChannelSpec"),
]

RELATIONSHIPS = [
    R("agent", "workflow", K.USAGE, "runs", "workflows", SH.REFS,
      help="an encoded process this agent may invoke"),
    R("trigger", "agent", K.ASSOCIATION, "fires", "agent", SH.REF,
      owner="source", target_mult="1", legacy="fires",
      help="unattended work: the trigger wakes this agent (ADR-0018)"),
    R("trigger", "workflow", K.ASSOCIATION, "starts", "workflow", SH.REF,
      target_mult="0..1"),
    R("channel", "agent", K.ASSOCIATION, "includes", "members", SH.REFS),
    R("workflow", "wrappable", K.REALIZATION, "", "", SH.REF,
      linkable=False, draw=Draw.NONE),
    *[R(k, "resource", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("workflow", "channel")],

    # -- a workflow is an Activity (ADR-0102) ---------------------------------
    R("workflow", "action", K.COMPOSITION, "step", "graph.nodes", SH.PART,
      source_mult="1", linkable=False, draw=Draw.NONE),
    R("workflow", "control_flow", K.COMPOSITION, "edge", "graph.edges",
      SH.PART, source_mult="1", linkable=False, draw=Draw.NONE),
    R("control_flow", "action", K.ASSOCIATION, "from", "source", SH.REF,
      target_mult="1", linkable=False, draw=Draw.NONE),
    R("control_flow", "action", K.ASSOCIATION, "to", "target", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{'END' = the ActivityFinalNode}"),
    R("action", "callable", K.USAGE, "calls", "tool", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = tool; 'c__op' = operation op of c}"),
    R("action", "agent", K.ASSOCIATION, "delegates to", "agent", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = agent}"),
    R("action", "workflow", K.ASSOCIATION, "invokes", "workflow", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = workflow}"),

    # -- the governed workflow and its seam to an engine (ADR-0110) ----------
    *[R(k, "step_owner", K.REALIZATION, "", "", SH.REF, linkable=False,
        draw=Draw.NONE) for k in ("agent", "team", "person")],
    R("action", "step_owner", K.ASSOCIATION, "owned by", "owner", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{an agent step defaults to its agent}",
      help="who does the step: an agent, a team or a person"),
    R("action", "person", K.ASSOCIATION, "approved by", "person", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = human}"),
    R("action", "role", K.ASSOCIATION, "approved by role", "role", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = human}"),
    R("workflow", "callable", K.USAGE, "interface calls", "interface.tools",
      SH.REFS, linkable=False, draw=Draw.NONE,
      constraint="{'c__op' = operation op of c}",
      help="what an external body calls; the calling step's owner must hold it"),
    R("workflow", "endpoint", K.USAGE, "interface reaches",
      "interface.endpoints", SH.REFS, linkable=False, draw=Draw.NONE),
    R("workflow", "data_class", K.ASSOCIATION, "receives",
      "interface.receives_data_classes", SH.REFS, linkable=False,
      draw=Draw.NONE,
      help="data sent to the body; the calling step's owner must hold it"),
    R("workflow", "data_class", K.ASSOCIATION, "returns",
      "interface.returns_data_classes", SH.REFS, linkable=False,
      draw=Draw.NONE),

    # -- ADR-0112: the rest of what process elements hold ---------------------
    R("workflow", "action", K.ASSOCIATION, "interrupts before",
      "interrupt_before", SH.REFS, linkable=False, draw=Draw.NONE,
      help="steps a person must release before they run"),
    R("ActivityGraph", "action", K.ASSOCIATION, "enters at", "entry",
      SH.REF, target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{empty = the first step}",
      help="where the InitialNode leads"),
    R("trigger", "channel", K.ASSOCIATION, "listens on", "channel", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE,
      constraint="{kind = message}"),
    R("trigger", "channel", K.ASSOCIATION, "delivers to", "deliver_to",
      SH.REFS, linkable=False, draw=Draw.NONE),
    R("FailurePolicy", "channel", K.ASSOCIATION, "notifies", "notify_channel",
      SH.REF, target_mult="0..1", linkable=False, draw=Draw.NONE),
    R("channel", "data_class", K.ASSOCIATION, "forbids",
      "forbid_data_classes", SH.REFS, linkable=False, draw=Draw.NONE),
    R("EscalationStep", "channel", K.ASSOCIATION, "on", "channel", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE),
    R("person", "channel", K.ASSOCIATION, "reached on", "channel", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE),
    R("HumanCounterpart", "channel", K.ASSOCIATION, "reached on", "channel",
      SH.REF, target_mult="0..1", linkable=False, draw=Draw.NONE),
    R("mission", "channel", K.ASSOCIATION, "works on", "channel", SH.REF,
      target_mult="0..1", linkable=False, draw=Draw.NONE),
    R("mission", "workflow", K.USAGE, "runs", "workflows", SH.REFS,
      linkable=False, draw=Draw.NONE),
]

ENUMERATIONS = [
    Enumeration(
        "ActivityNodeKind", "ActivityNodeKind",
        ("tool", "agent", "workflow", "transform", "branch", "human",
         "fork", "join"),
        "What a workflow step is: which UML ActivityNode it stands for.",
        uml=(("tool", "CallOperationAction"), ("agent", "CallBehaviorAction"),
             ("workflow", "CallBehaviorAction"), ("transform", "OpaqueAction"),
             ("branch", "DecisionNode"), ("human", "AcceptEventAction"),
             ("fork", "ForkNode"), ("join", "JoinNode"))),
    Enumeration(
        "WorkflowBody", "WorkflowBody", ("graph", "external"),
        "Where a workflow's insides live: drawn here as an Activity, or "
        "built in an engine the binding names (ADR-0110)."),
    Enumeration("TriggerKind", "TriggerKind",
                ("schedule", "event", "webhook", "message", "manual"),
                "What starts a run (ADR-0020)."),
    Enumeration("OverlapPolicy", "OverlapPolicy",
                ("skip", "queue", "cancel_previous", "allow"),
                "What to do when a run is still going and the next is due."),
    Enumeration("CatchUpPolicy", "CatchUpPolicy",
                ("skip_missed", "run_once", "run_all"),
                "What to do about runs missed while the system was down."),
    Enumeration("ChannelPurpose", "ChannelPurpose",
                ("notify", "approve", "handoff", "report", "ask"),
                "Why an agent contacts a human on a channel (ADR-0021)."),
    Enumeration("OutOfHours", "", ("queue", "escalate", "notify_anyway"),
                "What a human-facing channel does outside working hours."),
]

DATATYPES = [
    DataType("WorkflowInterface", "WorkflowInterface",
             "The seam between a governed workflow and a body built in an "
             "engine: what goes in and out, what it calls and which data it "
             "touches. Its references are the workflow's `interface calls`, "
             "`interface reaches`, `receives` and `returns` (ADR-0110)."),
    DataType("ActivityGraph", "ActivityGraph",
             "A workflow's steps and the flow between them: the Activity's "
             "nodes and edges (ADR-0102)."),
    DataType("Cadence", "Cadence",
             "When a schedule fires, readable by humans and targets."),
    DataType("FailurePolicy", "FailurePolicy",
             "What happens when a triggered run fails (ADR-0020)."),
    DataType("EscalationStep", "EscalationStep",
             "Who to try next when nobody answers on a channel, and when."),
]

PROPERTIES = [
    Property("action", "kind", "ActivityNodeKind", "1"),
    Property("workflow", "body", "WorkflowBody", "1",
             "graph unless the body is built in an engine"),
    Property("workflow", "interface", "WorkflowInterface", "1",
             "composite: the workflow owns its interface"),
    Property("WorkflowInterface", "inputs", "String", "0..*"),
    Property("WorkflowInterface", "outputs", "String", "0..*"),
    *props("workflow",
           "id: String", "name: String", "description: String",
           "inputs: Map -- the input schema"),
    *props("action",
           "id: String", "expr: String -- a transform's or branch's "
           "expression", "args: Map", "output: String -- the state key "
           "it writes",
           "cases: Any -- a branch's case → step map",
           "default: String -- the step a branch takes when no case "
           "matches, by id"),
    *props("trigger",
           "id: String", "description: String", "kind: TriggerKind",
           "cadence: Cadence [0..1] -- required for a schedule",
           "event_class: String -- an abstract event, for kind event",
           "filters: Map", "input: Map", "enabled: Boolean",
           "overlap: OverlapPolicy", "catch_up: CatchUpPolicy",
           "max_runtime_seconds: Integer", "requires_approval: Boolean",
           "failure: FailurePolicy"),
    *props("Cadence", "expression: String -- cron", "timezone: String"),
    *props("FailurePolicy",
           "retries: Integer", "backoff_seconds: Integer",
           "escalate_after_failures: Integer",
           "halt_after_consecutive_failures: Integer"),
    *props("channel",
           "id: String", "channel_class: ChannelClass",
           "description: String", "address: String",
           "human_facing: Boolean", "purposes: ChannelPurpose [0..*]",
           "working_hours: WorkingHours [0..1]",
           "response_sla_minutes: Integer [0..1]",
           "escalation: EscalationStep [0..*]",
           "out_of_hours: OutOfHours"),
    *props("EscalationStep",
           "after_minutes: Integer",
           "notify: String -- a human contact, an agent id or a team id",
           "note: String"),
]

PROFILE = Profile(
    name="Process", imports=("Core", "Organisation", "Authority", "Data"),
    doc="How work happens: workflows as Activities, triggers and channels.",
    stereotypes=STEREOTYPES, relationships=RELATIONSHIPS,
    enumerations=ENUMERATIONS, datatypes=DATATYPES, properties=PROPERTIES,
)
