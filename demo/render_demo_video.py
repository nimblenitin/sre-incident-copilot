from __future__ import annotations

import textwrap
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPT = ROOT / "demo" / "demo_recording.txt"
OUTPUT = ROOT / "demo" / "sre_incident_copilot_demo.mp4"

WIDTH = 1280
HEIGHT = 720
FPS = 12
MARGIN = 48
HEADER_HEIGHT = 76
LINE_HEIGHT = 24
MAX_LINES = 22

BG = (13, 18, 28)
PANEL = (20, 28, 42)
TEXT = (226, 232, 240)
MUTED = (139, 153, 174)
GREEN = (68, 211, 138)
AMBER = (245, 184, 75)
RED = (248, 113, 113)
BLUE = (96, 165, 250)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


TITLE_FONT = font(26)
BODY_FONT = font(18)
SMALL_FONT = font(14)


def simplify_transcript(raw: str) -> list[str]:
    replacements = {
        '{"status":"ok","mode":"read-only"}': '{"status":"ok","mode":"read-only"}',
        '"severity":"sev1"': '"severity":"sev1"',
        '"requires_human_approval":true': '"requires_human_approval":true',
    }
    lines: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            lines.append("")
            continue

        if line.startswith('{"incident_id"'):
            lines.extend(
                [
                    "{",
                    '  "service": "payment-api",',
                    '  "severity": "sev1",',
                    '  "slo_impact": {"burn_rate": 70.0, "is_error_budget_at_risk": true},',
                    '  "error_budget_status": {"status": "critical"},',
                    '  "recommended_runbook": "payment-api-error-rate",',
                    '  "diagnostic_commands": ["kubectl get pods", "kubectl logs", "curl /health"],',
                    '  "blocked_actions": ["rollback deployment", "scale deployment"],',
                    '  "requires_human_approval": true,',
                    '  "escalation_recommendation": {"target": "payments-sre"},',
                    '  "audit_log_id": "audit-..."',
                    "}",
                ]
            )
            continue

        if line.startswith('{"incident_id":"inc-demo"'):
            lines.extend(
                [
                    "{",
                    '  "title": "Blameless postmortem: payment-api sev1 incident",',
                    '  "impact": "Some customers could not complete payment.",',
                    '  "timeline": ["10:00: Alert fired", "10:05: Read-only diagnostics"],',
                    '  "follow_up_actions": ["Confirm root cause", "Update runbook", "Review SLO alerts"],',
                    '  "blameless_note": "Focus on systems and learning, not personal fault."',
                    "}",
                ]
            )
            continue

        if line.startswith('{"agent_version"'):
            lines.extend(
                [
                    "{",
                    '  "audit_log_id": "audit-...",',
                    '  "incident_id": "inc-...",',
                    '  "policy_version": "2026-05-22",',
                    '  "decision_reason": "SLO burn rate is critical; destructive actions blocked.",',
                    '  "human_owner": "sre-incident-commander"',
                    "}",
                ]
            )
            continue

        wrapped = textwrap.wrap(line, width=105, replace_whitespace=False) or [line]
        for item in wrapped:
            lines.append(replacements.get(item, item))
    return lines


def line_color(line: str) -> tuple[int, int, int]:
    lower = line.lower()
    if "blocked" in lower or "requires_human_approval" in lower or "critical" in lower:
        return RED
    if "sev1" in lower or "escalation" in lower or "burn_rate" in lower:
        return AMBER
    if "health" in lower or "read-only" in lower or "blameless" in lower:
        return GREEN
    if line[:2] in {"1.", "2.", "3.", "4.", "5."}:
        return BLUE
    return TEXT


def draw_frame(visible_lines: list[str], progress: float) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [28, 28, WIDTH - 28, HEIGHT - 28],
        radius=18,
        fill=PANEL,
        outline=(47, 61, 83),
        width=1,
    )
    draw.ellipse([56, 55, 70, 69], fill=RED)
    draw.ellipse([82, 55, 96, 69], fill=AMBER)
    draw.ellipse([108, 55, 122, 69], fill=GREEN)
    draw.text((MARGIN + 96, 50), "sre-incident-copilot demo", fill=TEXT, font=TITLE_FONT)
    draw.text((WIDTH - 270, 56), "safe read-only triage", fill=MUTED, font=SMALL_FONT)

    bar_width = int((WIDTH - 96) * progress)
    draw.rounded_rectangle([48, HEIGHT - 52, WIDTH - 48, HEIGHT - 42], radius=5, fill=(36, 48, 68))
    draw.rounded_rectangle([48, HEIGHT - 52, 48 + bar_width, HEIGHT - 42], radius=5, fill=BLUE)

    start = max(0, len(visible_lines) - MAX_LINES)
    y = MARGIN + HEADER_HEIGHT
    for line in visible_lines[start:]:
        prefix = "$ " if line and not line.startswith((" ", "{", "}", "[", '"')) and line[:2] not in {"1.", "2.", "3.", "4.", "5."} else ""
        draw.text((MARGIN, y), prefix + line, fill=line_color(line), font=BODY_FONT)
        y += LINE_HEIGHT

    draw.text((MARGIN, HEIGHT - 78), "Policy result: destructive actions are blocked and routed to human approval.", fill=MUTED, font=SMALL_FONT)
    return img


def main() -> None:
    raw = TRANSCRIPT.read_text(encoding="utf-8")
    lines = simplify_transcript(raw)
    frames = []

    total_steps = max(1, len(lines))
    for index in range(total_steps):
        visible = lines[: index + 1]
        frame = draw_frame(visible, index / total_steps)
        hold = 4 if lines[index].strip() else 2
        if lines[index].startswith(("1.", "2.", "3.", "4.", "5.")):
            hold = 10
        frames.extend([np.asarray(frame)] * hold)

    final = draw_frame(lines, 1.0)
    frames.extend([np.asarray(final)] * (FPS * 3))

    imageio.mimsave(OUTPUT, frames, fps=FPS, quality=8, macro_block_size=16)
    print(OUTPUT)


if __name__ == "__main__":
    main()
