"""Operational plane: structured logging, tracing, metrics and alerts.

Everything an operator needs to answer "what did the agents do, what did it
cost, and what needs attention" — computed from the same event store the
sessions write to, with an OpenTelemetry exporter when one is configured.
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter, defaultdict
from typing import Any, Optional

from .models import Agent, AgentSession, Alert, SessionEvent, SessionState, Severity
from .store import AGENTS, ALERTS, EVENTS, SESSIONS, Store

_LOG = logging.getLogger("orgagents")


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                base = {
                    "ts": self.formatTime(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
                base.update(getattr(record, "extra_fields", {}))
                return json.dumps(base)

        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    _LOG.handlers = [handler]
    _LOG.setLevel(level)
    _LOG.propagate = False


def log_event(message: str, **fields: Any) -> None:
    _LOG.info(message, extra={"extra_fields": fields})


class Observability:
    """Metrics, alert rules and dashboards over the session event store."""

    def __init__(self, store: Store, otel_exporter: Optional[Any] = None) -> None:
        self.store = store
        self.otel = otel_exporter
        # rule name -> (predicate over metrics dict, severity, message template)
        self.alert_rules: dict[str, tuple] = {
            "session_failure_rate": (
                lambda m: m["sessions"]["failed"] > 0
                and m["sessions"]["failed"] / max(m["sessions"]["total"], 1) > 0.2,
                Severity.ERROR,
                "More than 20% of sessions failed",
            ),
            "cost_budget": (
                lambda m: m["cost_usd"] > 500,
                Severity.WARNING,
                "Daily agent spend exceeded $500",
            ),
            "approval_backlog": (
                lambda m: m["sessions"]["waiting_human"] > 10,
                Severity.WARNING,
                "More than 10 sessions are waiting on a human",
            ),
            "tool_error_spike": (
                lambda m: m["errors"] > 25,
                Severity.ERROR,
                "Tool error count is above threshold",
            ),
        }

    # -- metrics -----------------------------------------------------------

    def metrics(self, agent_ids: Optional[set[str]] = None) -> dict[str, Any]:
        """Platform metrics; with `agent_ids`, only those agents' sessions
        and events count (a caller scoped to some workspaces, ADR-0116)."""
        sessions = self.store.list(SESSIONS, AgentSession, limit=5000)
        events = self.store.list(EVENTS, SessionEvent, limit=20000)
        if agent_ids is not None:
            sessions = [s for s in sessions if s.agent_id in agent_ids]
            kept = {s.id for s in sessions}
            events = [e for e in events if e.session_id in kept]
        by_state = Counter(s.state.value for s in sessions)
        by_type = Counter(e.type for e in events)
        per_agent: dict[str, dict[str, float]] = defaultdict(
            lambda: {"sessions": 0, "tokens": 0, "cost_usd": 0.0}
        )
        for s in sessions:
            a = per_agent[s.agent_id]
            a["sessions"] += 1
            a["tokens"] += s.token_usage
            a["cost_usd"] += s.cost_usd
        return {
            "agents": (self.store.count(AGENTS) if agent_ids is None
                       else len(agent_ids)),
            "sessions": {
                "total": len(sessions),
                "running": by_state.get(SessionState.RUNNING.value, 0),
                "waiting_human": by_state.get(SessionState.WAITING_HUMAN.value, 0),
                "completed": by_state.get(SessionState.COMPLETED.value, 0),
                "failed": by_state.get(SessionState.FAILED.value, 0),
            },
            "events": dict(by_type),
            "errors": by_type.get("error", 0),
            "tokens": sum(s.token_usage for s in sessions),
            "cost_usd": round(sum(s.cost_usd for s in sessions), 4),
            "per_agent": dict(per_agent),
        }

    # -- alerts ------------------------------------------------------------

    def evaluate_alerts(self) -> list[Alert]:
        m = self.metrics()
        fired: list[Alert] = []
        for name, (predicate, severity, message) in self.alert_rules.items():
            try:
                if predicate(m):
                    alert = Alert(severity=severity, title=name, detail=message)
                    self.store.put(ALERTS, alert, name=name)
                    fired.append(alert)
            except Exception as e:  # a broken rule must not break monitoring
                log_event("alert_rule_failed", rule=name, error=str(e))
        return fired

    def raise_alert(
        self,
        title: str,
        detail: str,
        *,
        severity: Severity = Severity.WARNING,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Alert:
        alert = Alert(
            severity=severity,
            title=title,
            detail=detail,
            agent_id=agent_id,
            session_id=session_id,
        )
        self.store.put(ALERTS, alert, name=title)
        log_event("alert", title=title, severity=severity.value, detail=detail)
        return alert

    def alerts(self, include_acknowledged: bool = False) -> list[Alert]:
        alerts = self.store.list(ALERTS, Alert, limit=500)
        return alerts if include_acknowledged else [a for a in alerts if not a.acknowledged]

    def acknowledge(self, alert_id: str) -> Optional[Alert]:
        a = self.store.get(ALERTS, alert_id, Alert)
        if a:
            a.acknowledged = True
            self.store.put(ALERTS, a)
        return a

    # -- tracing export ----------------------------------------------------

    def export_span(self, event: SessionEvent) -> None:
        """Forward one session event to the configured OTel exporter."""
        if self.otel is None:
            return
        self.otel.export(
            {
                "trace_id": event.trace_id,
                "span_id": event.span_id,
                "parent_span_id": event.parent_span_id,
                "name": event.type,
                "attributes": {"actor": event.actor, **event.payload},
                "timestamp": event.ts,
            }
        )

    def agent_health(self, agent_id: str) -> dict[str, Any]:
        agent = self.store.get(AGENTS, agent_id, Agent)
        sessions = self.store.list(SESSIONS, AgentSession, parent=agent_id, limit=1000)
        failed = sum(1 for s in sessions if s.state is SessionState.FAILED)
        return {
            "agent": agent.name if agent else agent_id,
            "sessions": len(sessions),
            "failed": failed,
            "failure_rate": round(failed / len(sessions), 3) if sessions else 0.0,
            "tokens": sum(s.token_usage for s in sessions),
            "cost_usd": round(sum(s.cost_usd for s in sessions), 4),
            "waiting_human": sum(
                1 for s in sessions if s.state is SessionState.WAITING_HUMAN
            ),
        }
