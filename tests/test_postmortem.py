from app.models import PostmortemRequest
from app.postmortem import draft_postmortem


def test_postmortem_draft_contains_required_sections() -> None:
    draft = draft_postmortem(
        PostmortemRequest(
            service="checkout-api",
            severity="sev2",
            summary="Checkout latency increased.",
            timeline=[{"time": "09:00", "event": "Latency alert fired"}],
        )
    )

    assert draft.executive_summary
    assert draft.contributing_factors
    assert draft.what_went_well
    assert draft.follow_up_actions
