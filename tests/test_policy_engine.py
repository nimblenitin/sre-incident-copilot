from app.policy_engine import PolicyEngine


def test_rollback_restart_and_scale_actions_are_blocked() -> None:
    engine = PolicyEngine()

    for action in ["rollback deployment payment-api", "restart pod checkout-api", "scale deployment api"]:
        blocked = engine.evaluate_action_text(action)
        assert blocked
        assert blocked[0].requires_human_approval is True
