from app.models import Alert, PostmortemRequest
from app.postmortem import draft_postmortem
from app.triage import TriageService


def test_high_error_rate_maps_to_high_severity(tmp_path) -> None:
    service = TriageService()

    response = service.triage(
        Alert(
            service="payment-api",
            alert_type="error_rate",
            summary="payment 5xx spike",
            metric_value=3.5,
            suggested_action="rollback deployment payment-api",
        )
    )

    assert response.severity == "sev1"
    assert response.escalation_recommendation.should_escalate is True
    assert response.requires_human_approval is True
    assert response.blocked_actions


def test_postmortem_draft_is_blameless_and_structured() -> None:
    draft = draft_postmortem(
        PostmortemRequest(
            incident_id="inc-test",
            service="payment-api",
            severity="sev1",
            summary="Elevated payment errors.",
            timeline=[{"time": "10:00", "event": "Alert fired"}],
            customer_impact="Some checkouts failed.",
        )
    )

    assert "blameless" in draft.title.lower()
    assert "fault" in draft.blameless_note.lower()
    assert draft.timeline
    assert draft.follow_up_actions
