from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import yaml

from app.audit_logger import AuditLogger
from app.models import AGENT_VERSION, Alert, EscalationRecommendation, TriageResponse
from app.policy_engine import PolicyEngine
from app.runbook_retriever import RunbookRetriever
from app.slo_engine import SLOEngine


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class TriageService:
    def __init__(
        self,
        config_dir: Path = DEFAULT_CONFIG_DIR,
        audit_logger: Optional[AuditLogger] = None,
    ) -> None:
        self.config_dir = config_dir
        self.policy_engine = PolicyEngine(config_dir)
        self.slo_engine = SLOEngine(config_dir)
        self.runbooks = RunbookRetriever(config_dir / "runbooks")
        self.audit_logger = audit_logger or AuditLogger()
        self.severity_matrix = self._load_yaml("severity_matrix.yaml")

    def _load_yaml(self, filename: str) -> dict:
        with (self.config_dir / filename).open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}

    def triage(self, alert: Alert) -> TriageResponse:
        incident_id = f"inc-{uuid4().hex[:10]}"
        slo_impact = self.slo_engine.calculate_impact(alert.service, alert.alert_type, alert.metric_value)
        error_budget_status = self.slo_engine.budget_status(slo_impact)
        severity, severity_reason = self._classify_severity(alert, slo_impact.burn_rate)
        runbook = self.runbooks.match(alert.service, alert.alert_type)

        runbook_actions = runbook.get("diagnostic_commands", []) if runbook else []
        diagnostic_commands, blocked_from_runbook = self.policy_engine.filter_runbook_actions(runbook_actions)
        blocked_from_alert = self.policy_engine.evaluate_action_text(alert.suggested_action)
        blocked_actions = blocked_from_runbook + blocked_from_alert
        requires_human_approval = bool(blocked_actions)

        probable_cause = self._probable_cause(alert, runbook)
        escalation = self._escalation(alert.service, severity, slo_impact.burn_rate, error_budget_status.status)
        decision_reason = (
            f"{severity_reason} SLO analysis: {slo_impact.reasoning} "
            f"Policy blocked {len(blocked_actions)} action(s)."
        )

        response = TriageResponse(
            incident_id=incident_id,
            timestamp=datetime.now(timezone.utc),
            agent_version=AGENT_VERSION,
            policy_version=self.policy_engine.policy_version,
            human_owner=self.policy_engine.human_owner,
            service=alert.service,
            severity=severity,
            probable_cause=probable_cause,
            slo_impact=slo_impact,
            error_budget_status=error_budget_status,
            recommended_runbook=runbook.get("id") if runbook else None,
            diagnostic_commands=diagnostic_commands,
            blocked_actions=blocked_actions,
            requires_human_approval=requires_human_approval,
            escalation_recommendation=escalation,
            slack_update_draft=self._slack_update(alert, incident_id, severity, probable_cause, escalation),
            audit_log_id="pending",
            decision_reason=decision_reason,
        )
        audit_id = self.audit_logger.write(response)
        return response.model_copy(update={"audit_log_id": audit_id})

    def _classify_severity(self, alert: Alert, burn_rate: float) -> tuple[str, str]:
        rules = self.severity_matrix.get("rules", [])
        normalized = alert.alert_type.lower()
        for rule in rules:
            alert_matches = rule.get("alert_type", "").lower() in normalized
            metric_matches = alert.metric_value is None or alert.metric_value >= float(rule.get("metric_min", 0))
            burn_matches = burn_rate >= float(rule.get("burn_rate_min", 0))
            service_matches = alert.service in rule.get("services", [alert.service])
            if alert_matches and metric_matches and burn_matches and service_matches:
                return rule["severity"], rule["reason"]
        return self.severity_matrix.get("default", "sev3"), "No high-priority rule matched."

    @staticmethod
    def _probable_cause(alert: Alert, runbook: Optional[dict]) -> str:
        if runbook and runbook.get("probable_causes"):
            return runbook["probable_causes"][0]
        if alert.description:
            return alert.description
        return "Cause unknown; start with read-only diagnostics from the matched service runbook."

    def _escalation(self, service: str, severity: str, burn_rate: float, budget_status: str) -> EscalationRecommendation:
        service_config = self.slo_engine.get_service(service)
        owner = service_config.get("owner", self.policy_engine.human_owner)
        should = severity in {"sev1", "sev2"} or burn_rate >= float(service_config.get("escalation_burn_rate", 2.0))
        reason = (
            f"Escalate to {owner}: severity={severity}, burn_rate={burn_rate}x, "
            f"error_budget_status={budget_status}."
            if should
            else "No immediate escalation required; continue observation and diagnostics."
        )
        return EscalationRecommendation(should_escalate=should, target=owner, reason=reason)

    @staticmethod
    def _slack_update(
        alert: Alert,
        incident_id: str,
        severity: str,
        probable_cause: str,
        escalation: EscalationRecommendation,
    ) -> str:
        escalation_text = f"Escalating to {escalation.target}." if escalation.should_escalate else "No escalation yet."
        return (
            f":rotating_light: Incident {incident_id} opened for {alert.service} ({severity}). "
            f"Signal: {alert.summary}. Probable cause: {probable_cause}. "
            f"Current mode: read-only diagnostics. {escalation_text}"
        )
