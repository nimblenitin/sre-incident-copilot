# Architecture

`sre-incident-copilot` is a FastAPI service with config-driven incident triage.

- `app/main.py` exposes API routes.
- `app/triage.py` orchestrates severity, SLO, runbook, policy, Slack draft, and audit decisions.
- `app/policy_engine.py` blocks destructive actions and enforces read-only governance.
- `app/slo_engine.py` estimates availability and error-budget burn from alert signals.
- `app/runbook_retriever.py` finds service runbooks from YAML.
- `app/audit_logger.py` appends JSONL records to `logs/audit.jsonl`.
- `app/postmortem.py` drafts structured blameless postmortems.

The service intentionally does not connect to Kubernetes, Terraform, CI/CD, or paging tools. It returns recommendations and traceable decisions only.
