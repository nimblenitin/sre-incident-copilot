from __future__ import annotations

from app.models import PostmortemDraft, PostmortemRequest


def draft_postmortem(request: PostmortemRequest) -> PostmortemDraft:
    timeline = [
        f"{item.get('time', 'unknown time')}: {item.get('event', 'event not provided')}"
        for item in request.timeline
    ]
    return PostmortemDraft(
        incident_id=request.incident_id,
        title=f"Blameless postmortem: {request.service} {request.severity} incident",
        executive_summary=(
            f"{request.service} experienced a {request.severity} incident. "
            f"{request.summary} This draft focuses on system behavior, signals, and process improvements."
        ),
        impact=request.customer_impact or "Customer impact is pending confirmation.",
        timeline=timeline,
        contributing_factors=[
            "Detection, diagnosis, and mitigation details should be validated with telemetry.",
            "Review alert thresholds, runbook coverage, and SLO burn-rate context.",
        ],
        what_went_well=[
            "The incident was captured in a traceable workflow.",
            "Read-only diagnostics preserved production safety while context was gathered.",
        ],
        follow_up_actions=[
            "Confirm root cause with service owners.",
            "Add or update runbook steps for any missing diagnostic paths.",
            "Review SLO alerting thresholds and escalation routing.",
        ],
        blameless_note=(
            "This postmortem is blameless: it describes conditions and decisions in the system, "
            "not personal fault. The goal is learning and safer future operations."
        ),
    )
