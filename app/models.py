from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


AGENT_VERSION = "0.1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Alert(BaseModel):
    service: str
    alert_type: str
    summary: str
    description: Optional[str] = None
    metric_value: Optional[float] = None
    metric_unit: Optional[str] = None
    threshold: Optional[float] = None
    suggested_action: Optional[str] = None
    labels: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


class SLOImpact(BaseModel):
    slo_target: float
    current_availability: Optional[float] = None
    burn_rate: float
    budget_consumed_percent: float
    is_error_budget_at_risk: bool
    reasoning: str


class ErrorBudgetStatus(BaseModel):
    status: str
    remaining_percent: float
    burn_rate: float


class BlockedAction(BaseModel):
    action: str
    reason: str
    requires_human_approval: bool = True


class EscalationRecommendation(BaseModel):
    should_escalate: bool
    target: str
    reason: str


class TriageResponse(BaseModel):
    incident_id: str
    timestamp: datetime
    agent_version: str
    policy_version: str
    human_owner: str
    service: str
    severity: str
    probable_cause: str
    slo_impact: SLOImpact
    error_budget_status: ErrorBudgetStatus
    recommended_runbook: Optional[str]
    diagnostic_commands: list[str]
    blocked_actions: list[BlockedAction]
    requires_human_approval: bool
    escalation_recommendation: EscalationRecommendation
    slack_update_draft: str
    audit_log_id: str
    decision_reason: str


class RunbookSummary(BaseModel):
    id: str
    service: str
    title: str
    alert_types: list[str]
    severity_hint: Optional[str] = None


class PostmortemRequest(BaseModel):
    incident_id: str = Field(default_factory=lambda: f"inc-{uuid4().hex[:10]}")
    service: str
    severity: str
    summary: str
    timeline: list[dict[str, Any]]
    customer_impact: Optional[str] = None
    detected_by: Optional[str] = None


class PostmortemDraft(BaseModel):
    incident_id: str
    title: str
    executive_summary: str
    impact: str
    timeline: list[str]
    contributing_factors: list[str]
    what_went_well: list[str]
    follow_up_actions: list[str]
    blameless_note: str
