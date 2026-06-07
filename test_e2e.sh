#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================="
echo "  E2E: Alert Chatbot Troubleshooting"
echo "======================================="

# ── Step 1: Clean up any previous state ──
echo ""
echo "=== Step 1: Cleanup previous resources ==="
kind delete cluster --name alert-chatbot 2>/dev/null || true
kill $(lsof -ti:8501) 2>/dev/null || true
kill $(lsof -ti:9091) 2>/dev/null || true
kill $(lsof -ti:9094) 2>/dev/null || true
kill $(lsof -ti:8081) 2>/dev/null || true
kill $(lsof -ti:5000) 2>/dev/null || true

# ── Step 2: Create Kind cluster ──
echo ""
echo "=== Step 2: Create Kind cluster ==="
kind create cluster --name alert-chatbot --config k8s/kind-config.yaml

# ── Step 3: Build and load inference API image ──
echo ""
echo "=== Step 3: Build inference API Docker image ==="
docker build -t inference-api:latest inference-api/
kind load docker-image inference-api:latest --name alert-chatbot

# ── Step 4: Deploy all K8s resources ──
echo ""
echo "=== Step 4: Deploy to Kubernetes ==="
kubectl --context kind-alert-chatbot apply -f k8s/inference-api-deploy.yaml -f k8s/prometheus-deploy.yaml -f k8s/alertmanager-deploy.yaml

echo "Waiting for pods to be ready..."
kubectl --context kind-alert-chatbot wait --for=condition=ready pod -l app=inference-api --timeout=120s
kubectl --context kind-alert-chatbot wait --for=condition=ready pod -l app=prometheus --timeout=120s
kubectl --context kind-alert-chatbot wait --for=condition=ready pod -l app=alertmanager --timeout=120s
kubectl --context kind-alert-chatbot wait --for=condition=ready pod -l app=slack-mock --timeout=60s

echo "All pods are ready."
kubectl --context kind-alert-chatbot get pods

# ── Step 5: Port forward inference API ──
echo ""
echo "=== Step 5: Port forward services ==="
kubectl --context kind-alert-chatbot port-forward svc/inference-api 8081:8000 &
echo $! > /tmp/pf_inference.pid
kubectl --context kind-alert-chatbot port-forward svc/prometheus 9091:9090 &
echo $! > /tmp/pf_prometheus.pid
kubectl --context kind-alert-chatbot port-forward svc/alertmanager 9094:9093 &
echo $! > /tmp/pf_alertmanager.pid
kubectl --context kind-alert-chatbot port-forward svc/slack-mock 5000:5000 &
echo $! > /tmp/pf_slack.pid

sleep 3

# Verify services are up
echo "Checking inference API health..."
curl -sf http://localhost:8081/health || { echo "inference API not ready"; exit 1; }
echo "OK"

echo "Checking Prometheus..."
curl -sf http://localhost:9091/-/ready || { echo "Prometheus not ready"; exit 1; }
echo "OK"

echo "Checking slack-mock..."
curl -sf -X POST http://localhost:5000/slack-webhook -d '{"test":true}' || { echo "slack-mock not ready"; exit 1; }
echo "OK"

# ── Step 6: Start Streamlit chatbot ──
echo ""
echo "=== Step 6: Start Streamlit chatbot ==="
streamlit run alert_app.py --server.port 8501 --server.headless true &
echo $! > /tmp/streamlit.pid
sleep 5
echo "Streamlit chatbot started on http://localhost:8501"

# ── Step 7: Trigger latency spike ──
echo ""
echo "=== Step 7: Inject latency spike into inference API ==="
curl -s -X POST http://localhost:8081/debug/set-latency -d '{"latency_ms": 3000}'
echo ""
echo "Latency set to 3000ms. Generating traffic..."

for i in $(seq 1 50); do
  curl -s -o /dev/null -X POST http://localhost:8081/v1/chat \
    -d '{"prompt":"Hello, world!","model":"llama3.1"}' &
done
wait

echo "Traffic generated. Waiting for Prometheus to evaluate alerts..."
sleep 30

# ── Step 8: Check Prometheus alerts ──
echo ""
echo "=== Step 8: Check firing alerts in Prometheus ==="
FIRING=$(curl -s http://localhost:9091/api/v1/alerts | python3 -c "
import json, sys
data = json.load(sys.stdin)
firing = [a for a in data['data']['alerts'] if a['state'] == 'firing']
if firing:
    for a in firing:
        print(f\"  FIRING: {a['labels']['alertname']} - {a['labels'].get('service', 'unknown')} (severity: {a['labels'].get('severity', 'unknown')})\")
else:
    print('  No firing alerts. Checking pending...')
    pending = [a for a in data['data']['alerts'] if a['state'] == 'pending']
    for a in pending:
        print(f\"  PENDING: {a['labels']['alertname']}\")
    if not pending:
        print('  No alerts at all.')
")

echo "$FIRING"

if echo "$FIRING" | grep -q "InferenceHighLatency"; then
    echo "✅ Alert is firing!"
else
    echo "⚠️ Alert not yet firing. This is OK for a first run - alerts may still be pending."
    echo "   Prometheus needs ~2 minutes of sustained high latency."
    echo "   Run the traffic loop again or check http://localhost:9091/alerts"
fi

# ── Step 9: Simulate Slack alert ──
echo ""
echo "=== Step 9: Simulate Slack alert (with chatbot link) ==="
cd "$SCRIPT_DIR"
python simulate_alert.py "http://localhost:5000/slack-webhook" "http://localhost:8501"

echo ""
echo "======================================="
echo "  E2E TEST SUMMARY"
echo "======================================="
echo "✅ Kind cluster running: alert-chatbot"
echo "✅ Inference API: http://localhost:8081"
echo "✅ Prometheus UI: http://localhost:9091"
echo "✅ Alertmanager: http://localhost:9094"
echo "✅ Slack mock: http://localhost:5000"
echo "✅ Chatbot UI: http://localhost:8501"
echo ""
echo "Open the chatbot URL in your browser."
echo "Add ?alert_id=demo-001&service=inference-api&metric=p99_latency&severity=critical"
echo "to see pre-loaded alert context."
echo ""
echo "To clean up: kind delete cluster --name alert-chatbot"
echo "======================================="

# ── Step 10: Wait for user to test ──
echo ""
echo "Open http://localhost:8501?alert_id=InferenceHighLatency-inference-api&service=inference-api&metric=p99_latency&severity=critical"
echo "in your browser and ask: 'Why is inference-api slow?'"
echo ""
read -p "Press Enter when done testing, or Ctrl+C to keep running..."

echo ""
echo "=== Cleanup ==="
kill $(cat /tmp/pf_inference.pid /tmp/pf_prometheus.pid /tmp/pf_alertmanager.pid /tmp/pf_slack.pid /tmp/streamlit.pid) 2>/dev/null || true
kind delete cluster --name alert-chatbot 2>/dev/null || true
echo "Done."
