"""Reference workflow library.

Three shapes that recur across enterprise agent work, encoded declaratively so
they can be rendered, versioned and reviewed in the designer UI.
"""
from __future__ import annotations

from typing import Optional

from ..models import WorkflowRef

WORKFLOW_LIBRARY: list[WorkflowRef] = [
    WorkflowRef(
        id="wfl_delegate_review",
        name="delegate-and-review",
        description=(
            "Manager pattern: split a brief into assignments, run them through "
            "direct reports, review the aggregate and escalate to the human "
            "counterpart when quality is below threshold."
        ),
        interrupt_before=["human_signoff"],
        input_schema={
            "brief": {"type": "string", "required": True},
            "assignee_ids": {"type": "array", "items": {"type": "string"}},
            "quality_threshold": {"type": "number", "default": 0.7},
        },
        graph={
            "entry": "plan",
            "nodes": [
                {
                    "id": "plan",
                    "kind": "tool",
                    "tool": "decompose_brief",
                    "args": {"brief": "{{ brief }}"},
                    "output": "plan",
                },
                {
                    "id": "delegate",
                    "kind": "agent",
                    "agent": "{{ assignee_ids[0] }}",
                    "args": {"task": "{{ plan }}"},
                    "output": "work",
                },
                {
                    "id": "review",
                    "kind": "tool",
                    "tool": "score_quality",
                    "args": {"work": "{{ work }}"},
                    "output": "score",
                },
                {
                    "id": "gate",
                    "kind": "branch",
                    "cases": [
                        {
                            "when": "score.get('value', 0) >= quality_threshold",
                            "to": "publish",
                        }
                    ],
                    "default": "human_signoff",
                },
                {"id": "human_signoff", "kind": "human", "output": "approval"},
                {
                    "id": "publish",
                    "kind": "tool",
                    "tool": "memory_write",
                    "args": {
                        "namespace": "deliverables",
                        "key": "{{ plan.get('title', 'untitled') }}",
                        "value": "{{ work }}",
                        "visibility": "protected",
                    },
                    "output": "published",
                },
            ],
            "edges": [
                {"from": "plan", "to": "delegate"},
                {"from": "delegate", "to": "review"},
                {"from": "review", "to": "gate"},
                {"from": "human_signoff", "to": "publish"},
                {"from": "publish", "to": "END"},
            ],
        },
    ),
    WorkflowRef(
        id="wfl_data_request",
        name="governed-data-request",
        description=(
            "Answer a business question from a system of record: resolve the "
            "schema, draft SQL, run it under the relational grant, and "
            "summarize with provenance."
        ),
        input_schema={"question": {"type": "string", "required": True},
                      "connection": {"type": "string", "required": True}},
        graph={
            "entry": "discover",
            "nodes": [
                {
                    "id": "discover",
                    "kind": "tool",
                    "tool": "db_warehouse__list_tables",
                    "args": {},
                    "output": "tables",
                },
                {
                    "id": "draft_sql",
                    "kind": "tool",
                    "tool": "draft_sql",
                    "args": {"question": "{{ question }}", "tables": "{{ tables }}"},
                    "output": "sql",
                },
                {
                    "id": "execute",
                    "kind": "tool",
                    "tool": "db_warehouse__query",
                    "args": {"sql": "{{ sql }}"},
                    "output": "rows",
                },
                {
                    "id": "summarize",
                    "kind": "tool",
                    "tool": "summarize_rows",
                    "args": {"question": "{{ question }}", "rows": "{{ rows }}"},
                    "output": "answer",
                },
            ],
            "edges": [
                {"from": "discover", "to": "draft_sql"},
                {"from": "draft_sql", "to": "execute"},
                {"from": "execute", "to": "summarize"},
                {"from": "summarize", "to": "END"},
            ],
        },
    ),
    WorkflowRef(
        id="wfl_cross_team_request",
        name="cross-team-request",
        description=(
            "Route a request that leaves the agent's remit: find the owning "
            "team through the org chart, message its agent over the enterprise "
            "channel, and track the response."
        ),
        graph={
            "entry": "route",
            "nodes": [
                {
                    "id": "route",
                    "kind": "tool",
                    "tool": "find_owner_agent",
                    "args": {"topic": "{{ topic }}"},
                    "output": "owner",
                },
                {
                    "id": "notify",
                    "kind": "tool",
                    "tool": "send_message",
                    "args": {
                        "to_agent_id": "{{ owner.get('id') }}",
                        "subject": "{{ topic }}",
                        "body": "{{ request }}",
                        "requires_response": True,
                    },
                    "output": "message",
                },
                {
                    "id": "await_reply",
                    "kind": "tool",
                    "tool": "await_reply",
                    "args": {"message_id": "{{ message.get('id') }}"},
                    "output": "reply",
                },
            ],
            "edges": [
                {"from": "route", "to": "notify"},
                {"from": "notify", "to": "await_reply"},
                {"from": "await_reply", "to": "END"},
            ],
        },
    ),
]


def workflow_by_name(name: str) -> Optional[WorkflowRef]:
    return next((w for w in WORKFLOW_LIBRARY if w.name == name), None)
