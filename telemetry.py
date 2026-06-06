import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

AUDIT_DIR = Path("./audit_logs")


class TelemetrySession:
    """Records every agent interaction into a structured JSON audit log."""

    def __init__(
        self,
        alert_id: str,
        service: str = "unknown",
        metric: str = "",
        severity: str = "info",
    ):
        self.session_id = str(uuid.uuid4())
        self.alert_id = alert_id
        self.service = service
        self.metric = metric
        self.severity = severity
        self.start_time = datetime.now(timezone.utc)
        self.interactions: list[dict] = []
        self.approval_requested = False
        self.approval_time: datetime | None = None
        self._feedback_given = False
        self._feedback_helped: bool | None = None
        self._feedback_actual_fix: str = ""
        self.current_trace_id: str | None = None
        self.tool_calls_history: list[str] = []
        self.completed_diagnostic_steps: list[str] = []
        repeat_count = TelemetrySession.get_repeat_count(metric)
        if repeat_count > 0:
            self._write({
                "event": "session_start",
                **self._base(),
                "repeat_count": repeat_count,
                "repeat_of_metric": metric,
            })
        else:
            self._write({"event": "session_start", **self._base()})

    def set_trace_id(self, trace_id: str):
        self.current_trace_id = trace_id
        self.tool_calls_history = []
        self.completed_diagnostic_steps = []

    def _base(self) -> dict:
        d = {
            "session_id": self.session_id,
            "alert_id": self.alert_id,
            "service": self.service,
            "metric": self.metric,
            "severity": self.severity,
        }
        if self.current_trace_id:
            d["trace_id"] = self.current_trace_id
        return d

    def _write(self, entry: dict):
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        filepath = AUDIT_DIR / f"{self.session_id}.jsonl"
        entry["_timestamp"] = datetime.now(timezone.utc).isoformat()
        with open(filepath, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def log_tool_call(
        self,
        tool_name: str,
        args: dict,
        result: str,
        duration_ms: float,
        owner_team: str = "Unknown",
        reason_code: str = "",
    ):
        self.tool_calls_history.append(tool_name)
        if tool_name == "get_next_step":
            cs = args.get("completed_steps", "")
            if cs:
                for step in cs.split(","):
                    s = step.strip()
                    if s and s not in self.completed_diagnostic_steps:
                        self.completed_diagnostic_steps.append(s)
        entry = {
            "event": "tool_call",
            **self._base(),
            "tool_name": tool_name,
            "owner_team": owner_team,
            "reason_code": reason_code,
            "args": args,
            "result_preview": result[:500],
            "duration_ms": round(duration_ms, 1),
        }
        self._write(entry)

    def log_interaction(
        self,
        user_query: str,
        agent_response: str,
        duration_ms: float,
        has_irreversible_suggestion: bool = False,
        irreversible_reason: str | None = None,
        confidence: float = 0.0,
        cited_runbooks: list[str] | None = None,
        cited_sections: list[str] | None = None,
    ):
        entry = {
            "event": "interaction",
            **self._base(),
            "user_query": user_query,
            "agent_response": agent_response[:2000],
            "duration_ms": round(duration_ms, 1),
            "has_irreversible_suggestion": has_irreversible_suggestion,
            "irreversible_reason": irreversible_reason,
            "confidence": round(confidence, 2),
            "cited_runbooks": cited_runbooks or [],
            "cited_sections": cited_sections or [],
        }
        self.interactions.append(entry)
        self._write(entry)

    def log_decision_trace(
        self,
        intent: str,
        context_retrieved: list[str] | None = None,
        constraint_checks: list[dict] | None = None,
        policies_applied: list[str] | None = None,
        confidence: float = 1.0,
    ):
        entry = {
            "event": "decision_trace",
            **self._base(),
            "intent": intent,
            "confidence": round(confidence, 2),
            "context_retrieved": context_retrieved or [],
            "constraint_checks": constraint_checks or [],
            "policies_applied": policies_applied or [],
            "tool_chain": list(self.tool_calls_history),
        }
        self._write(entry)

    def log_approval_requested(
        self,
        suggestion_text: str = "",
        irreversible_reason: str = "",
        confidence: float = 0.0,
        user_confirmed: bool = False,
    ):
        self.approval_requested = True
        self.approval_time = datetime.now(timezone.utc)
        self._write({
            "event": "approval_requested",
            **self._base(),
            "suggestion_text": suggestion_text[:2000],
            "irreversible_reason": irreversible_reason,
            "confidence": round(confidence, 2),
            "user_confirmed": user_confirmed,
        })

    def log_resolution_feedback(
        self,
        helped: bool | None,
        actual_fix: str = "",
    ):
        self._feedback_given = True
        self._feedback_helped = helped
        self._feedback_actual_fix = actual_fix
        self._write({
            "event": "resolution_feedback",
            **self._base(),
            "helped": helped,
            "actual_fix": actual_fix[:2000],
        })

    def log_ticket_closed(self):
        now = datetime.now(timezone.utc)
        mttr_seconds = (now - self.start_time).total_seconds()
        self._write({
            "event": "ticket_closed",
            **self._base(),
            "session_start": self.start_time.isoformat(),
            "close_time": now.isoformat(),
            "mttr_seconds": round(mttr_seconds, 1),
        })
        return mttr_seconds

    def get_audit_trail(self) -> list[dict]:
        return list(self.interactions)

    def get_summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "alert_id": self.alert_id,
            "service": self.service,
            "metric": self.metric,
            "severity": self.severity,
            "start_time": self.start_time.isoformat(),
            "interaction_count": len(self.interactions),
            "approval_requested": self.approval_requested,
            "approval_time": self.approval_time.isoformat()
            if self.approval_time
            else None,
            "feedback_given": self._feedback_given,
            "feedback_helped": self._feedback_helped,
            "feedback_actual_fix": self._feedback_actual_fix[:500] if self._feedback_actual_fix else "",
        }

    @staticmethod
    def get_repeat_count(metric: str) -> int:
        if not metric or not AUDIT_DIR.exists():
            return 0
        seen_sessions: set[str] = set()
        for fpath in sorted(AUDIT_DIR.glob("*.jsonl"), reverse=False):
            sid = fpath.stem
            for line in fpath.read_text().strip().split("\n"):
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "session_start" and ev.get("metric") == metric:
                    seen_sessions.add(sid)
                    break
        return len(seen_sessions)

    @staticmethod
    def list_sessions() -> list[dict]:
        if not AUDIT_DIR.exists():
            return []
        sessions: dict[str, dict] = {}
        for fpath in sorted(AUDIT_DIR.glob("*.jsonl"), reverse=True):
            sid = fpath.stem
            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "first_event": None,
                    "event_count": 0,
                    "summary": None,
                    "repeat_count": 0,
                }
            for line in fpath.read_text().strip().split("\n"):
                if not line:
                    continue
                ev = json.loads(line)
                sessions[sid]["event_count"] += 1
                if sessions[sid]["first_event"] is None:
                    sessions[sid]["first_event"] = ev.get("_timestamp")
                if ev.get("event") == "session_start":
                    sessions[sid]["summary"] = ev
                    sessions[sid]["repeat_count"] = ev.get("repeat_count", 0)
        return list(sessions.values())
