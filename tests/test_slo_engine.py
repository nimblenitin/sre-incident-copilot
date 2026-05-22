from app.slo_engine import SLOEngine


def test_slo_burn_rate_affects_escalation_signal() -> None:
    engine = SLOEngine()

    impact = engine.calculate_impact("payment-api", "error_rate", 3.5)

    assert impact.burn_rate >= 2
    assert impact.is_error_budget_at_risk is True
    assert engine.budget_status(impact).status == "critical"
