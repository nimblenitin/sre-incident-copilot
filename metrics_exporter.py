"""Prometheus metrics exporter for SRE Co-Pilot audit data.
Reads audit_logs/*.jsonl and exposes metrics at /metrics.

Usage:
    python metrics_exporter.py          # runs on port 9100
    python metrics_exporter.py --port 9101
"""
import json
import os
import time
import argparse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict

AUDIT_DIR = Path("./audit_logs")

METRICS_HEADER = """# HELP sre_sessions_total Total number of SRE sessions
# TYPE sre_sessions_total counter
# HELP sre_tickets_closed_total Total number of closed tickets
# TYPE sre_tickets_closed_total counter
# HELP sre_tickets_open Current number of open (unclosed) tickets — backlog
# TYPE sre_tickets_open gauge
# HELP sre_reopen_count Reopen count per metric (number of prior sessions)
# TYPE sre_reopen_count gauge
# HELP sre_mttr_seconds Time-to-resolve per closed session
# TYPE sre_mttr_seconds gauge
# HELP sre_feedback_total Total feedback responses
# TYPE sre_feedback_total counter
"""


def collect_metrics() -> str:
    lines = []
    metric_sessions: dict[str, int] = defaultdict(int)
    metric_reopens: dict[str, int] = defaultdict(int)
    mttrs: list[dict] = []
    feedback: dict[str, int] = defaultdict(int)

    if not AUDIT_DIR.exists():
        return METRICS_HEADER + "# audit_logs directory not found\n"

    total_sessions = 0
    total_closed = 0

    for fpath in sorted(AUDIT_DIR.glob("*.jsonl")):
        session_id = fpath.stem
        session_metric = None
        session_service = None
        session_repeat = 0
        has_session = False

        for line in fpath.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            event = ev.get("event")

            if event == "session_start":
                session_metric = ev.get("metric", "unknown")
                session_service = ev.get("service", "unknown")
                session_repeat = ev.get("repeat_count", 0)
                has_session = True
                total_sessions += 1
                if session_metric:
                    metric_reopens[session_metric] = max(
                        metric_reopens.get(session_metric, 0), session_repeat
                    )
                    metric_sessions[session_metric] += 1

            elif event == "ticket_closed":
                total_closed += 1
                mttr = ev.get("mttr_seconds")
                if mttr is not None:
                    mttrs.append({
                        "session_id": session_id,
                        "metric": session_metric or ev.get("metric", "unknown"),
                        "service": session_service or ev.get("service", "unknown"),
                        "mttr": mttr,
                    })

            elif event == "resolution_feedback" and has_session:
                helped = ev.get("helped")
                if helped is True:
                    feedback["helped_true"] += 1
                elif helped is False:
                    feedback["helped_false"] += 1
                else:
                    feedback["helped_unknown"] += 1

    # Total sessions counter
    lines.append(f"sre_sessions_total {total_sessions}")

    # Closed tickets counter
    lines.append(f"sre_tickets_closed_total {total_closed}")

    # Open tickets gauge (backlog)
    open_tickets = total_sessions - total_closed
    lines.append(f"sre_tickets_open {max(open_tickets, 0)}")

    # Reopen gauge per metric
    for metric, count in sorted(metric_reopens.items()):
        lines.append(f'sre_reopen_count{{metric="{metric}"}} {count}')

    # MTTR gauge per session
    for m in mttrs:
        lines.append(
            f'sre_mttr_seconds{{session_id="{m["session_id"][:8]}",'
            f'metric="{m["metric"]}",service="{m["service"]}"}} {m["mttr"]}'
        )

    # Feedback counter
    for label, count in sorted(feedback.items()):
        lines.append(f'sre_feedback_total{{helped="{label}"}} {count}')

    return METRICS_HEADER + "\n".join(lines) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(collect_metrics().encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="SRE Co-Pilot metrics exporter")
    parser.add_argument("--port", type=int, default=9100, help="Port to listen on")
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), MetricsHandler)
    print(f"[metrics_exporter] Listening on :{args.port} — serving /metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[metrics_exporter] Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
