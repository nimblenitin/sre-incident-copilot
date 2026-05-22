# Case Study

An elevated 5xx alert fires for `payment-api`.

1. The copilot classifies the alert as `sev1` using `severity_matrix.yaml`.
2. The SLO engine compares the observed error rate with the 99.95% availability SLO.
3. The runbook retriever selects `payment-api-error-rate`.
4. The policy engine allows read-only commands such as logs and health checks.
5. Rollback and scaling steps are blocked because they can change production state.
6. The response recommends escalation to `payments-sre`.
7. A Slack-style update is drafted and the full decision is recorded in `logs/audit.jsonl`.

The result is governed incident assistance: fast context assembly without autonomous remediation.
