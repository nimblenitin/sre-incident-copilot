from __future__ import annotations

import json
from pathlib import Path
from typing import Union
from uuid import uuid4

from pydantic import BaseModel


DEFAULT_LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "audit.jsonl"


class AuditLogger:
    def __init__(self, log_path: Path = DEFAULT_LOG_PATH) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: Union[BaseModel, dict]) -> str:
        audit_log_id = f"audit-{uuid4().hex[:12]}"
        payload = event.model_dump(mode="json") if isinstance(event, BaseModel) else dict(event)
        payload["audit_log_id"] = audit_log_id

        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, sort_keys=True) + "\n")

        return audit_log_id
