import requests
import json
import sys
import os

SLACK_WEBHOOK_URL = os.environ.get(
    "SLACK_WEBHOOK_URL",
    sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000/slack-webhook",
)
STREAMLIT_URL = os.environ.get(
    "STREAMLIT_URL",
    sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8501",
)

alert_payload = {
    "channel": "#sre-alerts",
    "username": "Alertmanager",
    "icon_emoji": ":warning:",
    "attachments": [
        {
            "color": "danger",
            "title": "⚠️ InferenceHighLatency - inference-api",
            "text": (
                "*Description:* p99 inference latency is 3.2s for inference-api\n"
                "*Severity:* critical\n"
                "*Service:* inference-api\n"
                "*Runbook:* high-latency\n"
            ),
            "actions": [
                {
                    "type": "button",
                    "text": "🔧 Troubleshoot with AI",
                    "url": (
                        f"{STREAMLIT_URL}/?alert_id=InferenceHighLatency-inference-api"
                        f"&service=inference-api"
                        f"&metric=p99_latency"
                        f"&value=3.2s"
                        f"&severity=critical"
                    ),
                }
            ],
            "footer": "AI SRE Troubleshooting Agent",
            "ts": 1717200000,
        }
    ],
}

try:
    response = requests.post(SLACK_WEBHOOK_URL, json=alert_payload, timeout=10)
    print(f"Sent alert to Slack webhook: {SLACK_WEBHOOK_URL}")
    print(f"Response: {response.status_code} {response.text}")
    print(f"\nChatbot URL: {STREAMLIT_URL}/")
    print("Open this in your browser to start troubleshooting.")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
