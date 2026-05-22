# Seven Habits Mapping

This project demonstrates the Seven Habits of Effective Agentic Systems in an SRE incident setting.

1. Clear objective: triage incidents, gather context, and communicate status without remediating production.
2. Bounded autonomy: policies enforce read-only behavior and mark risky actions for human approval.
3. Context retrieval: service metadata, severity rules, SLOs, and runbooks are loaded from versioned YAML.
4. Tool discipline: diagnostic commands are suggested, not executed.
5. Memory and traceability: every triage decision is written to JSONL audit logs.
6. Human collaboration: escalation recommendations route to service owners and incident commanders.
7. Continuous learning: postmortem drafts convert incidents into structured follow-up actions.
