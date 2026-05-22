import json

from app.audit_logger import AuditLogger
from app.models import Alert
from app.triage import TriageService


def test_every_triage_request_writes_audit_log(tmp_path) -> None:
    log_path = tmp_path / "audit.jsonl"
    service = TriageService(audit_logger=AuditLogger(log_path))

    response = service.triage(
        Alert(
            service="checkout-api",
            alert_type="high_latency",
            summary="latency high",
            metric_value=1500,
            suggested_action="restart deployment checkout-api",
        )
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["incident_id"] == response.incident_id
    assert payload["audit_log_id"] == response.audit_log_id
