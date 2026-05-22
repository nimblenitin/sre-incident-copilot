from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from app.models import RunbookSummary


DEFAULT_RUNBOOK_DIR = Path(__file__).resolve().parents[1] / "config" / "runbooks"


class RunbookRetriever:
    def __init__(self, runbook_dir: Path = DEFAULT_RUNBOOK_DIR) -> None:
        self.runbook_dir = runbook_dir

    def load_all(self) -> list[dict[str, Any]]:
        runbooks: list[dict[str, Any]] = []
        for path in sorted(self.runbook_dir.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
                data["_path"] = str(path)
                runbooks.append(data)
        return runbooks

    def list_for_service(self, service: str) -> list[RunbookSummary]:
        return [
            RunbookSummary(
                id=runbook["id"],
                service=runbook["service"],
                title=runbook["title"],
                alert_types=runbook.get("alert_types", []),
                severity_hint=runbook.get("severity_hint"),
            )
            for runbook in self.load_all()
            if runbook.get("service") == service
        ]

    def match(self, service: str, alert_type: str) -> Optional[dict[str, Any]]:
        normalized = alert_type.lower()
        service_runbooks = [item for item in self.load_all() if item.get("service") == service]
        for runbook in service_runbooks:
            if any(alert.lower() in normalized or normalized in alert.lower() for alert in runbook.get("alert_types", [])):
                return runbook
        return service_runbooks[0] if service_runbooks else None
