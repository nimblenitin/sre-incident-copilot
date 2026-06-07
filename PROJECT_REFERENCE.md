# SRE Alert Troubleshooting Chatbot — Project Reference

## Overview

A read-only diagnostic AI agent that lives inside every alert notification. When an on-call engineer clicks a Slack alert, a Streamlit web UI opens pre-loaded with that alert's context (service, metric, severity). The engineer asks questions and the agent suggests diagnostic commands and runbook steps — but never executes anything.

The system runs on a local Kind Kubernetes cluster with Prometheus alerting, Alertmanager, a simulated inference API with Prometheus metrics, and a Slack webhook mock for end-to-end testing.

---

## Architecture (End-to-End Data Flow)

```
Inference API (FastAPI)                            Kind K8s Cluster
  exposes /metrics (Prometheus)
         |
         v
Prometheus scrapes /metrics
  evaluates alert rules
         |
         v
Alertmanager receives firing alert
  formats Slack message with button + chatbot link
         |
         v
Slack webhook mock (localhost:5000)
  prints alert + chatbot URL to console
         |
         v
Engineer clicks "Troubleshoot with AI" button
         |
         v
  Streamlit UI (localhost:8501)
  loads alert context from URL query params
  creates a TelemetrySession (audit log)
  embeds alert context in system prompt
  user types a question → ReActAgent
    tool 1: get_runbook_steps(metric) → direct file lookup by metric name
    tool 2: get_next_step(metric, completed_steps) → state machine for diagnostic ordering
    tool 3: suggest_diagnostic_command(service, symptom) → text
    tool 4: assess_options(service, situation) → tradeoff analysis
    tool 5: propose_manifest(service, change_type, params) → YAML manifest
  post-processing replaces hallucinated content with runbook data
  escalation gate for irreversible suggestions (checkbox + Approve)
  feedback form + close ticket button after resolution
         |
         v
Audit trail (audit_logs/*.jsonl)
  → metrics_exporter.py exposes Prometheus metrics on :9100
  → Grafana dashboard visualizes MTTR, backlog, reopen count, feedback
```

---

## Component-by-Component Breakdown

### 1. Inference API (`inference-api/app.py`)

A FastAPI server that simulates an ML inference service with Prometheus metrics.

**Endpoints:**
- `GET /health` — returns `{"status": "ok"}`
- `POST /v1/chat` — simulates inference with configurable latency (default 50-300ms), exposes histograms, counters, and a gauge
- `GET /metrics` — Prometheus metrics endpoint
- `POST /debug/set-latency` — override latency for alert testing
- `POST /debug/reset-latency` — reset to random latency

**Prometheus metrics exposed:**
- `inference_latency_seconds` — Histogram with exemplars
- `inference_requests_total` — Counter, labelled by status
- `inference_errors_total` — Counter, labelled by error_type
- `inference_requests_in_flight` — Gauge

**Alert rules** (in `prometheus/alert.rules.yml`):
- `InferenceHighLatency` — p99 latency > 1.5s for 1min (critical)
- `InferenceErrorRateHigh` — error rate > 5% for 2min (warning)
- `InferenceNoTraffic` — zero requests for 5min (warning)
- `InferenceHighInFlight` — in-flight > 10 for 1min (warning)

**Dockerfile** — multi-stage, exposes port 8000, installs `inference-api/requirements.txt`.

### 2. Prometheus + Alertmanager (K8s manifests in `k8s/`)

Prometheus scrapes the inference API at `inference-api:8000/metrics` every 15s and evaluates alert rules. When an alert fires, it sends to Alertmanager, which routes to a Slack webhook.

**Alertmanager config** (`prometheus/alertmanager.yml`):
- Single receiver `slack-sre` posting to `http://localhost:5000/slack-mock`
- Slack message includes a "Troubleshoot with AI" button with URL:
  ```
  http://localhost:8501?alert_id={{alertname}}-{{service}}&service={{service}}&metric=p99_latency&severity={{severity}}
  ```

**Prometheus also scrapes:** `host.docker.internal:9100` (the `metrics_exporter.py` on the host) for SRE Co-Pilot audit metrics (`sre_sessions_total`, `sre_mttr_seconds`, `sre_reopen_count`, `sre_feedback_total`, `sre_tickets_open`).

**K8s resources under `k8s/`:**
- `kind-config.yaml` — Kind cluster definition with port mappings
- `inference-api-deploy.yaml` — Deployment + Service for the inference API
- `prometheus-deploy.yaml` — ConfigMap with prometheus.yml + alert.rules.yml + metrics_exporter scrape target, Deployment, Service (NodePort 30090)
- `prometheus-rbac.yaml` — ServiceAccount + ClusterRole + binding for Prometheus pod discovery
- `alertmanager-deploy.yaml` — ConfigMap with alertmanager.yml, Deployment, Service (NodePort 30091)

### 3. Slack Webhook Mock (`slack_webhook_server.py`)

A minimal Python HTTP server listening on port 5000. When Alertmanager POSTs a Slack webhook, it prints the full JSON payload and the chatbot URL to the console. Used for local testing instead of a real Slack workspace.

### 4. Simulate Alert (`simulate_alert.py`)

A script that POSTs a mock Slack alert payload (with chatbot button) to the webhook. Reads `SLACK_WEBHOOK_URL` and `STREAMLIT_URL` from env vars or CLI args. Used to simulate an alert without Prometheus.

### 5. Agent (`alert_chatbot.py`)

A `ReActAgent` from `llama-index-core` with Ollama (`llama3.1:8b`) as the LLM.

**Note on model evolution:** Tested models: `qwen2.5:1.5b` (too small), `llama3.2:3b` (fast ~10-20s but rarely calls tools), `qwen3:8b` (calls tools reliably but needs `_QwenOllama` subclass for its thinking-field bug, ~60s), `llama3.1:8b` (current default — calls tools reliably, ~10-20s, no subclass needed).

**Settings:**
- `context_window=8192`
- `temperature=0.0`

**Five tools:**

1. **`get_runbook_steps(metric)`** — Direct file lookup of runbook content by metric name via `RUNBOOK_MAP`. No similarity search, no embedding. Reads the markdown file from disk and returns its full content as JSON with `result`, `irreversible`, `reason`. When the runbook is updated, the agent picks up the change instantly — no reindex needed.

2. **`get_next_step(metric, completed_steps)`** — Runbook state machine. Defines the diagnostic step sequence per metric in `DIAGNOSTIC_WORKFLOWS` (e.g. `p99_latency`: check health → check latency metrics → check pod resources → assess options → propose manifest). The agent passes a comma-separated list of completed steps and gets back the next step. State lives in the tool call, not the conversation history.

3. **`suggest_diagnostic_command(service, symptom)`** — Keyword-matches the symptom against a small map to return a diagnostic shell command as JSON with `command`, `irreversible`, `reason`. Uses `COMMAND_IRREVERSIBILITY_MAP` to determine irreversibility per symptom type.

4. **`assess_options(service, situation)`** — Generates structured tradeoff analysis with 2-3 remediation options. Each option includes: name, reversibility description, risk level, and tradeoffs. Returns JSON with `suggestion`, `has_irreversible_suggestion`, `irreversible_reason`, `confidence`.

5. **`propose_manifest(service, change_type, params)`** — Accepts a `change_type` enum (`config_update`, `scale_replicas`, `env_update`, `resource_limits`, `rollback`) and `params` JSON string. Returns a complete K8s YAML manifest as JSON with `manifest`, `irreversible`, `reason`. All change types except `resource_limits` return `irreversible: true`.

**`TOOL_OWNERS` dict:**
Every tool is tagged with a human team. If a tool call goes wrong, the ticket routes to the owning team, not "the AI team."
- `get_runbook_steps`, `get_next_step` → SRE Runbook Team
- `suggest_diagnostic_command`, `assess_options` → SRE Engineering
- `propose_manifest` → Platform Team

**`_derive_reason_code(tool_name, args)` — deterministic reason codes:**
Every tool call carries a `reason_code` derived from the tool name and arguments at the telemetry wrapper layer — the LLM contributes nothing to it. Examples:
- `get_runbook_steps(metric="p99_latency")` → `"lookup_runbook_for_p99_latency"`
- `get_next_step(metric="p99_latency", completed_steps="")` → `"query_first_diagnostic_step"`
- `get_next_step(metric="p99_latency", completed_steps="check_health_endpoint")` → `"query_next_diagnostic_step"`
- `suggest_diagnostic_command(service="inference-api", symptom="latency")` → `"diagnostic_command_for_latency"`
- `assess_options(service="inference-api")` → `"assess_remediation_for_inference-api"`
- `propose_manifest(service="inference-api", change_type="rollback")` → `"propose_rollback_change"`

**Key detail — `_extract()` guard:**
Small models (3B-8B) have broken function calling. They often pass the JSON schema properties dict as the argument value instead of a plain string. `_extract()` walks nested dicts to find the actual text, preventing wasted retries.

**`_make_normalized_tool()` wrapper:**
Uses `functools.wraps` to preserve the original function's `inspect.signature()` even when the wrapper uses `**kwargs`. This ensures `FunctionTool` generates correct tool schemas that the LLM can parse.

**`COMMAND_IRREVERSIBILITY_MAP` + `_keyword_check()`:**
Deterministic irreversibility detection using:
- `_keyword_check()` scans runbook text for irreversible phrases
- `COMMAND_IRREVERSIBILITY_MAP` defines per-symptom irreversibility (for suggest_diagnostic_command)

**`make_agent(telemetry_session, system_prompt)` factory:**
Creates a `ReActAgent` with 5 tools, optional telemetry wrapping, and an optional system prompt. All 5 tools are wrapped with `_make_normalized_tool()` and optionally `wrap_tool_with_telemetry()`. The agent's `run()` call enforces `max_iterations=8` with `early_stopping_method='generate'`.

**`AgentResponse` Pydantic model:**
```python
class AgentResponse(BaseModel):
    reasoning: str
    has_irreversible_suggestion: bool = False
    irreversible_reason: str | None = None
    confidence: float = 0.0
    manifest_yaml: str = ""
```

**`parse_agent_response(text)` parser:**
Three-layer fallback chain:
1. Try JSON parsing from ` ```json ` blocks or trailing `{...}`
2. Fall back to `===ASSESSMENT===` delimiter parsing (Irreversible:/Reason:/Confidence: fields)
3. Return graceful defaults (reasoning=full text, no irreversible, confidence=0.0)

### 7. Streamlit UI (`alert_app.py`)

Single-page web app that receives alert context via URL query parameters.

**URL format:**
```
http://localhost:8501/?alert_id=InferenceHighLatency-inference-api&service=inference-api&metric=p99_latency&severity=critical
```

**Layout:**
- Title + alert context in an expandable code block (includes reopen count)
- Text area for the engineer's question (pre-filled with a default query)
- "Diagnose" button → runs the agent (~10-20s on llama3.1:8b)
- Agent response displayed as markdown
- Escalation gate for irreversible suggestions (manifest: checkbox + "Approve" button; informational: red warning only)
- Post-resolution feedback form (Yes/Partially/No + what you actually did)
- "Close ticket" button (records MTTR)
- Optional checkbox for verbose reasoning trace

**Post-processing in `run_agent_sync()`:**
1. Pre-fetches runbook data via `get_runbook_steps(metric=metric)`
2. Extracts diagnostic commands (`_extract_runbook_commands()`) and resolution options (`_extract_runbook_resolution()`)
3. If runbook contains "Option 1" — agent response is **replaced entirely** with runbook content (3B-8B model reliably hallucinates over runbook data)
4. Otherwise — diagnostic commands are **prepended** to the agent response
5. Extracts `manifest_yaml` from code blocks (`_extract_manifest()`)
6. Runs `_assess_irreversibility()` keyword-scan on final text
7. Sets `confidence = 0.8 if runbook_data_exists else 0.3`
8. If manifest YAML found, `has_irreversible_suggestion = True` regardless of keyword scan

**Escalation gate — two modes:**
- **Manifest proposal:** YAML code block + "I have reviewed the manifest and approve this change" checkbox + "Approve" button. On approve: manifest written to `/tmp/<alert_id>_manifest.yaml`, audit logged.
- **Upgrade decision / informational:** Red error banner with reason. No checkbox, no button. Engineer evaluates independently.

**Feedback form (post-resolution):**
- Radio: "Did the agent's suggestion help resolve the issue?" — Yes / Partially / No
- Text area: "What did you actually do to fix it?" (shown if No or Partially)
- Submit logs `resolution_feedback` event to audit trail
- Hidden during the manifest approval gate

**Close ticket:**
- Button visible after response is shown and approval gate is resolved
- Records `ticket_closed` event with `mttr_seconds = close_time - session_start_time`
- Displays MTTR in UI (e.g., "MTTR: 14.2m")

**Repeat tracking:**
- On session start, `TelemetrySession.get_repeat_count(metric)` scans prior audit logs
- Reopen count displayed in alert context expander
- Logged in `session_start` event as `repeat_count`

**System prompt construction:**
Alert context is embedded directly in the system prompt. The prompt uses explicit numbered ordering (1-7) with no conditional permissions — the agent must follow the sequence without deciding to skip steps. It includes a citation instruction ("cite the section name — e.g. 'Per [Section: Diagnostic Steps]...'") so that `cited_sections` can be traced back to workflow state.

**Trace ID and decision trace:**
On each "Diagnose" click, `alert_app.py` generates a single `trace_id` (UUID) and calls `telemetry.set_trace_id(trace_id)`. After the agent responds, `run_agent_sync()` calls `telemetry.log_decision_trace()` with:
- `intent` — hardcoded as `"diagnose_{metric}"` from the alert URL param (not LLM-generated)
- `context_retrieved` — runbook path from `RUNBOOK_MAP`
- `constraint_checks` — metric threshold info from `METRIC_THRESHOLDS` (e.g. `{"metric":"p99_latency","threshold":"<500ms p99","unit":"ms"}`)
- `policies_applied` — all diagnostic workflow step labels for the metric from `DIAGNOSTIC_WORKFLOWS`
- `tool_chain` — ordered list of tools actually called (from `telemetry.tool_calls_history`)

**`_derive_cited_sections()`:**
After the agent run, `_derive_cited_sections(metric, telemetry.completed_diagnostic_steps)` maps the completed step IDs (tracked from `get_next_step(completed_steps=...)` args) to human-readable labels using the same `DIAGNOSTIC_WORKFLOWS` state machine. Result is logged in the `interaction` event as `cited_sections`. No text scanning, no heuristic matching.

**Event loop handling (critical):**
`ReActAgent.run()` uses `asyncio.create_task()` internally. Streamlit uses:
- `nest_asyncio.apply()` — allows re-entrant event loop usage
- `_get_loop()` — caches a single event loop in `st.session_state` (never closed)
- `loop.run_until_complete()` — runs the agent synchronously

**Session identity and port forwarding:**
- Streamlit view: `http://localhost:8501`
- Inference API: `http://localhost:8081` (mapped from port 8000)
- Prometheus: `http://localhost:9091` (mapped from port 9090)
- Alertmanager: `http://localhost:9094` (mapped from port 9093)
- Slack mock: `http://localhost:5000`
- Metrics exporter: `http://localhost:9100`

### 8. Audit Telemetry (`telemetry.py`)

Logs every session to a JSONL file under `audit_logs/<session_id>.jsonl`.

**Events logged (all carry `trace_id` — one UUID per agent run, shared across all events in that run):**
- `session_start` — UUID, alert metadata, timestamp, `repeat_count` (if applicable)
- `tool_call` — `trace_id`, tool name, `owner_team` (from `TOOL_OWNERS`), `reason_code` (from `_derive_reason_code()` — deterministic, not LLM-generated), args, result preview (truncated to 500 chars), `duration_ms`
- `decision_trace` — `trace_id`, `intent` (hardcoded from alert metric URL param as `"diagnose_{metric}"`), `confidence`, `context_retrieved` (runbook path), `constraint_checks` (metric thresholds with threshold/unit), `policies_applied` (all diagnostic workflow steps for the metric), `tool_chain` (ordered list of tools called)
- `interaction` — `trace_id`, user query, agent response (truncated to 2000 chars), `duration_ms`, `cited_runbooks` (runbook file paths), `cited_sections` (human-readable step labels derived from `completed_diagnostic_steps` via `_derive_cited_sections()`), `has_irreversible_suggestion`, `irreversible_reason`, `confidence`
- `approval_requested` — `trace_id`, suggestion text, irreversible_reason, confidence, user_confirmed
- `resolution_feedback` — `trace_id`, helped (bool or None), actual_fix (string)
- `ticket_closed` — `trace_id`, session_start time, close_time, mttr_seconds

**Methods:**
- `set_trace_id(trace_id)` — sets the current trace_id and resets `tool_calls_history` and `completed_diagnostic_steps`
- `log_tool_call(tool_name, args, result, duration_ms, owner_team, reason_code)` — also tracks `completed_diagnostic_steps` by extracting step IDs from `get_next_step(completed_steps=...)` args
- `log_decision_trace(intent, context_retrieved, constraint_checks, policies_applied, confidence)` — records the full decision context alongside `tool_chain`
- `log_interaction(user_query, agent_response, duration_ms, cited_runbooks, cited_sections, ...)` — includes policy citation metadata
- `log_approval_requested(suggestion_text, irreversible_reason, confidence, user_confirmed)`
- `log_resolution_feedback(helped, actual_fix)`
- `log_ticket_closed()` — returns mttr_seconds
- `get_audit_trail()` — returns list of interaction dicts
- `get_summary()` — session metadata, interaction count, feedback state, approval state
- `get_repeat_count(metric)` — static method, counts prior sessions for the same metric
- `list_sessions()` — static method, returns summary of every recorded session

### 9. Metrics Exporter (`metrics_exporter.py`)

Standalone HTTP server that reads `audit_logs/*.jsonl` and exposes Prometheus metrics at `/metrics` on port `9100`.

**Metrics exposed:**
- `sre_sessions_total` — counter of all sessions
- `sre_tickets_closed_total` — counter of closed tickets
- `sre_tickets_open` — gauge (sessions_total - tickets_closed_total, backlog)
- `sre_reopen_count{metric="..."}` — gauge per metric
- `sre_mttr_seconds{session_id, metric, service}` — gauge per closed ticket
- `sre_feedback_total{helped="helped_true|helped_false|helped_unknown"}` — counter

### 10. Grafana Dashboard (`config/sre-grafana-dashboard.json`)

Importable Grafana dashboard with 6 panels:
- Total Incidents (stat), Avg MTTR (stat with thresholds), Open Tickets / Backlog (stat), Helpful vs Unhelpful (pie chart)
- Reopen Count by Metric (bar gauge), Ticket Backlog over Time (time series), MTTR per Incident (time series)

### 11. Makefile

`make all` — docker build → kind deploy → e2e test
`make build` — docker build + kind load
`make deploy` — kubectl apply
`make test` — run e2e test script
`make chatbot` — start Streamlit
`make slack-mock` — start Slack webhook server
`make simulate` — send mock alert
`make metrics` — start metrics_exporter on :9100
`make clean` — delete Kind cluster + local index

---

## Key Constraints and Design Decisions

| Constraint | Implementation |
|---|---|---|
| Agent is read-only | 5 tools return text/JSON only; no mutation tools; system prompt says "Never apply changes directly" |
| Alert context without extra API call | Context embedded in system prompt via URL query params |
| LLM model | `llama3.1:8b` via `Ollama(model="llama3.1:8b")` — reliable tool calling, ~10-20s |
| Small model hallucinates over runbook data | Post-processing replaces agent response with actual runbook content when structured options exist |
| Deterministic irreversibility (not LLM self-assessment) | `_assess_irreversibility()` regex-scan; `_keyword_check()` on runbook text; `COMMAND_IRREVERSIBILITY_MAP` for diagnostic commands |
| Deterministic reason codes (not LLM-generated) | `_derive_reason_code(tool_name, args)` generates `reason_code` for every tool call at the telemetry wrapper layer — zero model involvement |
| Tool ownership for accountability | `TOOL_OWNERS` dict tags every tool with a human team; `owner_team` logged in every `tool_call` event |
| Decision trace with trace_id | One `trace_id` (UUID) per agent run shared by all events; `decision_trace` event records intent, context, constraints, policies, tool_chain |
| Consecutive clicks in Streamlit | Cached event loop in `st.session_state` + `nest_asyncio.apply()` |
| Irreversible action gate | Two modes: manifest (checkbox + "Approve" button → writes YAML to /tmp) vs informational (red warning only) |
| Broken function-calling in small models | `_extract()` guard unwraps schema-object args |
| MTTR tracking | "Close ticket" button records close time, computes mttr_seconds |
| Reopen tracking | `get_repeat_count()` scans prior audit logs for same metric; displayed in alert context |
| Feedback loop | Post-resolution form logs helped/actual_fix to audit trail |
| Metrics observable in Grafana | `metrics_exporter.py` → Prometheus scrape → importable dashboard in `config/sre-grafana-dashboard.json` |
| Port conflicts | Inference API on 8081, Prometheus on 9091, Alertmanager on 9094 |

---

## Habits Applied

- **Habit 1 (Clearly Bounded Role):** One job — suggest what to check next. Never runs commands, never mutates state. Five read-only tools, all capabilities enumerated in system prompt. Hallucinated tool calls discarded.

- **Habit 2 (Embedded in Workflows):** Slots into existing on-call pipeline. Prometheus → Alertmanager → Slack → engineer clicks → agent appears. Nothing about the alert workflow changes; the agent just makes finding the next step faster.

- **Habit 3 (Explicit Constraints):** No mutation tools, `max_iterations=8` on `run()`, `_extract()` guard, system prompt with explicit numbered ordering (1-6, no conditional language — the agent must follow the sequence without permission to stop early).

- **Habit 4 (Defers Irreversibility):** Deterministic keyword scanning replaces LLM self-assessment. Two-mode escalation gate: manifest proposals require checkbox + "Approve" button; upgrade decisions show red warning only. The agent proposes, the engineer disposes.

- **Habit 5 (Optimizes for System Outcomes):** Post-resolution feedback form tracks whether suggestions helped. Close ticket button records MTTR. Reopen tracking surfaces incomplete fixes. `metrics_exporter.py` feeds audit data into Prometheus/Grafana for aggregate MTTR comparison between agent-assisted and unassisted resolutions.

- **Habit 6 (Progress Through Structure):** `get_runbook_steps` replaces RAG with direct file lookup — no similarity gamble, no reindex needed. `get_next_step` implements the runbook state machine in code — the agent calls it to determine what to do next rather than tracking state in conversation. `propose_manifest` uses typed enums. `_assess_irreversibility()` is deterministic regex — code beats model. `AgentResponse` is a Pydantic model with typed fields. Post-processing replaces hallucinated output with structured runbook data.

- **Habit 7 (Visible Accountability):** Every agent run generates one `trace_id` (UUID) shared by all events in that run — `session_start`, `tool_call` (with `owner_team` from `TOOL_OWNERS` and deterministic `reason_code` from `_derive_reason_code()`), `decision_trace` (records intent, context_retrieved, constraint_checks, policies_applied, tool_chain), `interaction` (with `cited_runbooks` and `cited_sections` derived from completed diagnostic steps), `approval_requested`, `resolution_feedback`, and `ticket_closed`. Filter events by `trace_id` to reconstruct the full causal chain — intent through tool calls to final response. The `decision_trace` event answers "why did the agent do that" without replaying the model. Full audit trail accessible for post-incident review via `audit_logs/*.jsonl`.

---

## Known Issues

1. **Full tool chain not guaranteed** — The agent reliably calls `get_runbook_steps` and `get_next_step` but does not always reach `suggest_diagnostic_command`, `assess_options`, or `propose_manifest`. The system prompt now uses explicit numbered ordering (1-6, no conditional language) to push harder, but the 8B model may still stop early. Mitigated by post-processing (runbook content replacement) which fills in diagnostic commands and resolution options.
2. **CPU-only inference** — ~10-20s per query on llama3.1:8b. No GPU available in current environment.
3. **Single-turn only** — Each Diagnose click is independent. No chat history carried across turns.
4. **`RUNBOOK_MAP` must be updated manually** — Adding a new runbook requires editing `RUNBOOK_MAP` in `alert_chatbot.py` to map the metric name to the file path. Not automated.
