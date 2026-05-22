#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

echo "sre-incident-copilot short demo"
echo "================================="
echo

echo "1. Health check"
curl -s "${BASE_URL}/health"
echo
echo

echo "2. Available runbooks for payment-api"
curl -s "${BASE_URL}/runbooks/payment-api"
echo
echo

echo "3. Triage a payment-api elevated 5xx alert"
curl -s -X POST "${BASE_URL}/triage" \
  -H "Content-Type: application/json" \
  --data @examples/alert_error_rate.json
echo
echo

echo "4. Draft a blameless postmortem"
curl -s -X POST "${BASE_URL}/postmortem/draft" \
  -H "Content-Type: application/json" \
  -d '{"incident_id":"inc-demo","service":"payment-api","severity":"sev1","summary":"Elevated 5xx rate affected checkout completion.","timeline":[{"time":"10:00","event":"Alert fired"},{"time":"10:05","event":"On-call began read-only diagnostics"}],"customer_impact":"Some customers could not complete payment."}'
echo
echo

echo "5. Latest audit log entry"
tail -n 1 logs/audit.jsonl
echo
