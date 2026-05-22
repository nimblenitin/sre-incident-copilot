# sre-incident-copilot

`sre-incident-copilot` is a governed, read-only SRE incident assistant. It helps on-call engineers triage alerts, reason about SLO and error-budget impact, retrieve runbooks, suggest safe diagnostic commands, draft Slack-style updates, and write audit logs.

It is not an autonomous remediation bot. It never restarts pods, scales services, rolls back deployments, deletes data, applies Terraform, or permanently silences alerts. When an alert or runbook suggests an irreversible action, the copilot blocks it and returns `requires_human_approval: true`.

## SRE Workflow

When an alert arrives, the service:

1. Classifies severity from `config/severity_matrix.yaml`.
2. Checks SLO impact from `config/services.yaml`.
3. Retrieves a matching YAML runbook from `config/runbooks/`.
4. Filters runbook actions through `config/policies.yaml`.
5. Recommends escalation when severity or burn rate warrants it.
6. Drafts an incident update suitable for Slack.
7. Appends a traceable JSONL audit event to `logs/audit.jsonl`.

Trace fields include `incident_id`, `timestamp`, `agent_version`, `policy_version`, `decision_reason`, and `human_owner`.

## Seven Habits

The project demonstrates the Seven Habits of Effective Agentic Systems:

- Clear objective: support incident triage, not remediation.
- Bounded autonomy: dangerous actions are blocked by policy.
- Context retrieval: decisions are grounded in service metadata, SLOs, severity rules, and runbooks.
- Tool discipline: diagnostic commands are suggested but not executed.
- Traceability: every decision is written to an audit log.
- Human collaboration: escalation routes to owners and incident commanders.
- Continuous learning: postmortem drafts turn incident facts into improvement actions.

## Setup

```bash
cd sre-incident-copilot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## API Examples

Triage a payment error-rate alert:

```bash
curl -s -X POST http://127.0.0.1:8000/triage \
  -H "Content-Type: application/json" \
  --data @examples/alert_error_rate.json
```

List runbooks for a service:

```bash
curl http://127.0.0.1:8000/runbooks/payment-api
```

Draft a postmortem:

```bash
curl -s -X POST http://127.0.0.1:8000/postmortem/draft \
  -H "Content-Type: application/json" \
  -d '{
    "incident_id": "inc-demo",
    "service": "payment-api",
    "severity": "sev1",
    "summary": "Elevated payment 5xx rate affected checkout completion.",
    "timeline": [
      {"time": "10:00", "event": "Alert fired"},
      {"time": "10:05", "event": "On-call began read-only diagnostics"}
    ],
    "customer_impact": "Some customers could not complete payment."
  }'
```

## Sample Output

```json
{
  "incident_id": "inc-1234567890",
  "service": "payment-api",
  "severity": "sev1",
  "probable_cause": "Payment processor dependency errors or recent deployment regression.",
  "error_budget_status": {
    "status": "critical",
    "remaining_percent": 0.0,
    "burn_rate": 70.0
  },
  "recommended_runbook": "payment-api-error-rate",
  "diagnostic_commands": [
    "kubectl get pods -n payments -l app=payment-api",
    "kubectl logs -n payments deploy/payment-api --since=15m | grep ERROR",
    "curl -s https://payment-api.example.com/health"
  ],
  "blocked_actions": [
    {
      "action": "rollback deployment payment-api",
      "reason": "Read-only policy blocks destructive or irreversible action matching pattern: \\brollback\\b",
      "requires_human_approval": true
    }
  ],
  "requires_human_approval": true
}
```

## Tests

```bash
pytest
```

The tests cover severity classification, policy blocking for rollback/restart/scale, SLO burn-rate escalation, audit logging, and blameless postmortem structure.
