from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from app.models import ErrorBudgetStatus, SLOImpact


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class SLOEngine:
    def __init__(self, config_dir: Path = DEFAULT_CONFIG_DIR) -> None:
        self.config_dir = config_dir
        self.services = self._load_yaml("services.yaml").get("services", {})

    def _load_yaml(self, filename: str) -> dict[str, Any]:
        with (self.config_dir / filename).open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}

    def get_service(self, service_name: str) -> dict[str, Any]:
        return dict(self.services.get(service_name, {}))

    def calculate_impact(self, service_name: str, alert_type: str, metric_value: Optional[float]) -> SLOImpact:
        service = self.get_service(service_name)
        slo_target = float(service.get("availability_slo", 99.0))
        budget = 100.0 - slo_target

        current_availability = self._estimate_availability(alert_type, metric_value)
        if current_availability is None:
            burn_rate = 1.0
            consumed = 0.0
            reasoning = "No direct availability metric was supplied; using conservative default burn rate."
        else:
            observed_error = max(0.0, 100.0 - current_availability)
            burn_rate = round(observed_error / budget, 2) if budget > 0 else 999.0
            consumed = min(100.0, round(burn_rate * 10.0, 2))
            reasoning = (
                f"Observed availability {current_availability:.3f}% compared with "
                f"{slo_target}% SLO leaves an error-budget burn rate of {burn_rate}x."
            )

        return SLOImpact(
            slo_target=slo_target,
            current_availability=current_availability,
            burn_rate=burn_rate,
            budget_consumed_percent=consumed,
            is_error_budget_at_risk=burn_rate >= float(service.get("escalation_burn_rate", 2.0)),
            reasoning=reasoning,
        )

    def budget_status(self, impact: SLOImpact) -> ErrorBudgetStatus:
        remaining = max(0.0, round(100.0 - impact.budget_consumed_percent, 2))
        if impact.burn_rate >= 5:
            status = "critical"
        elif impact.burn_rate >= 2:
            status = "at_risk"
        else:
            status = "healthy"

        return ErrorBudgetStatus(status=status, remaining_percent=remaining, burn_rate=impact.burn_rate)

    @staticmethod
    def _estimate_availability(alert_type: str, metric_value: Optional[float]) -> Optional[float]:
        if metric_value is None:
            return None
        normalized = alert_type.lower()
        if "error" in normalized or "5xx" in normalized:
            return max(0.0, 100.0 - metric_value)
        if "latency" in normalized:
            penalty = min(5.0, max(0.0, (metric_value - 500.0) / 500.0))
            return max(0.0, 99.9 - penalty)
        if "crashloop" in normalized or "pod" in normalized:
            return max(0.0, 100.0 - metric_value)
        return None
