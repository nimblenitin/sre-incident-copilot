from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml

from app.models import BlockedAction


DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class PolicyEngine:
    """Applies read-only governance rules before the copilot recommends action."""

    def __init__(self, config_dir: Path = DEFAULT_CONFIG_DIR) -> None:
        self.config_dir = config_dir
        self.policy = self._load_yaml("policies.yaml")
        self.policy_version = str(self.policy.get("version", "unknown"))
        self.human_owner = str(self.policy.get("human_owner", "sre-oncall"))

    def _load_yaml(self, filename: str) -> dict[str, Any]:
        with (self.config_dir / filename).open("r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}

    @property
    def dangerous_patterns(self) -> list[str]:
        return list(self.policy.get("blocked_action_patterns", []))

    def evaluate_action_text(self, action_text: Optional[str]) -> list[BlockedAction]:
        if not action_text:
            return []

        blocked: list[BlockedAction] = []
        for pattern in self.dangerous_patterns:
            if re.search(pattern, action_text, flags=re.IGNORECASE):
                blocked.append(
                    BlockedAction(
                        action=action_text,
                        reason=(
                            "Read-only policy blocks destructive or irreversible "
                            f"action matching pattern: {pattern}"
                        ),
                    )
                )
                break
        return blocked

    def filter_runbook_actions(self, actions: list[str]) -> tuple[list[str], list[BlockedAction]]:
        allowed: list[str] = []
        blocked: list[BlockedAction] = []

        for action in actions:
            matches = self.evaluate_action_text(action)
            if matches:
                blocked.extend(matches)
            else:
                allowed.append(action)

        return allowed, blocked
