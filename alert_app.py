import streamlit as st
import asyncio
import nest_asyncio
import json
import uuid
from datetime import datetime, timezone
from alert_chatbot import make_agent, AgentResponse, parse_agent_response
from telemetry import TelemetrySession

nest_asyncio.apply()

st.set_page_config(page_title="SRE Troubleshooting Agent", layout="wide")
st.title("🔧 Alert Diagnostic Agent")

query_params = st.query_params
alert_id = query_params.get("alert_id", [None])
if isinstance(alert_id, list):
    alert_id = alert_id[0]

service = query_params.get("service", ["unknown"])
if isinstance(service, list):
    service = service[0]

metric = query_params.get("metric", [""])
if isinstance(metric, list):
    metric = metric[0]

severity = query_params.get("severity", ["info"])
if isinstance(severity, list):
    severity = severity[0]

if not alert_id:
    st.warning("No alert ID provided. Please open this link from an alert.")
    st.stop()

# ── Telemetry session ──
if "telemetry" not in st.session_state:
    st.session_state.telemetry = TelemetrySession(
        alert_id=alert_id,
        service=service,
        metric=metric,
        severity=severity,
    )
    from telemetry import TelemetrySession as _TS
    st.session_state.repeat_count = _TS.get_repeat_count(metric)

telemetry = st.session_state.telemetry
repeat_count = st.session_state.repeat_count

# ── Session state for escalation gate ──
if "last_response" not in st.session_state:
    st.session_state.last_response = None
if "irreversible_pending" not in st.session_state:
    st.session_state.irreversible_pending = False
if "approval_done" not in st.session_state:
    st.session_state.approval_done = False
if "feedback_submitted" not in st.session_state:
    st.session_state.feedback_submitted = False
if "show_feedback" not in st.session_state:
    st.session_state.show_feedback = False
if "ticket_closed" not in st.session_state:
    st.session_state.ticket_closed = False
if "mttr_seconds" not in st.session_state:
    st.session_state.mttr_seconds = None

_PROMPT_TEMPLATE = """SRE agent. Never run commands yourself.

Call these tools in order for every alert. Do not ask the user what to do next — just call the next tool.

1. get_runbook_steps(metric) — FIRST. Direct runbook lookup by metric name.
2. get_next_step(metric, completed_steps) — SECOND. Determine the first diagnostic step.
3. suggest_diagnostic_command(service, symptom) — Get the command for the current step.
4. Call get_next_step again with updated completed_steps. Repeat steps 3-4 until no more steps.
5. assess_options(service, situation) — Generate tradeoff analysis for remediation.
6. propose_manifest(service, change_type, params) — Propose a K8s manifest if a change is needed.
7. Respond to the user with findings. When you reference runbook content, cite the section name — e.g. "Per [Section: Diagnostic Steps] in the runbook, the first step is..."

Fallback: If get_runbook_steps returns no result, use search_runbooks(query) instead.

When presenting options, format each one clearly with its label, reversibility, risk level, tradeoffs, and end with a recommendation. Here is a reference example:

Situation: API pods are OOMKilling under load; HPA is exhausted and cannot absorb further traffic spikes.

Option 1 — Increase memory limits on the Deployment
Reversible. Low risk. Gives pods headroom immediately without restarts. Tradeoff: if the leak is real, you're buying time not fixing root cause — and you're changing a live Deployment manifest.

Option 2 — Roll back to the previous Deployment revision
Reversible. Medium risk. If this started after the last deploy, a rollback may eliminate the cause entirely. Tradeoff: you lose whatever was in the new release, and if the problem predates the deploy you've changed nothing useful while adding a rollback event to the audit trail.

Option 3 — Delete the leaking pods and force a reschedule
Irreversible in effect. High risk. Pods are recreated but in-flight requests are dropped. Active user sessions are severed. There is no undo — traffic is disrupted the moment you execute.

Recommendation: Option 1 to stabilise, then Option 2 if telemetry shows the leak began at deploy time. Option 3 only if p99 exceeds SLO breach threshold and nothing else has worked.

Now handle this alert:

Alert: {alert_id} | Service: {service} | Metric: {metric} | Severity: {severity}"""

MAX_ITERATIONS = 8


JSON_DEFAULT = {"result": "", "irreversible": False, "reason": ""}


def _extract_runbook_commands(text: str) -> str:
    """Extract diagnostic commands from the first runbook result only."""
    lines = []
    for line in text.split("\n"):
        if line.startswith("--- Result 2 "):
            break
        stripped = line.strip()
        if stripped.startswith("curl ") or stripped.startswith("kubectl "):
            lines.append(stripped)
    return "\n".join(lines)


def _extract_runbook_resolution(text: str) -> str:
    """Extract the resolution options section from the first runbook result."""
    capture = False
    lines = []
    for line in text.split("\n"):
        if line.startswith("--- Result 2 "):
            break
        if "### Resolution Options" in line or "### Resolution" in line:
            capture = True
            continue
        if capture:
            if line.startswith("###") or line.startswith("---"):
                break
            if line.strip():
                lines.append(line)
    return "\n".join(lines).strip()


def _extract_manifest(text: str) -> str:
    """Extract a K8s manifest YAML block from text (between ```yaml and ```)."""
    import re
    for match in re.finditer(r"```(?:yaml)?\s*\n(.*?)```", text, re.DOTALL):
        candidate = match.group(1).strip()
        if "apiVersion" in candidate and "kind" in candidate:
            return candidate
    return ""


def _derive_cited_sections(metric: str, completed_steps: list[str]) -> list[str]:
    """Determine cited runbook sections from completed diagnostic steps.

    Relies on the diagnostic workflow state machine, not text scanning.
    Each completed step ID maps to a human-readable section name.
    """
    from alert_chatbot import DIAGNOSTIC_WORKFLOWS
    step_labels = {
        "check_health_endpoint": "Check Health Endpoint",
        "check_latency_metrics": "Check Latency Metrics",
        "check_pod_resources": "Check Pod Resources",
        "assess_options": "Assess Remediation Options",
        "propose_manifest": "Propose Manifest Change",
        "check_error_logs": "Check Error Logs",
        "check_recent_deploy": "Check Recent Deploy",
        "check_pool_metrics": "Check Connection Pool Metrics",
        "check_connection_count": "Check Connection Count",
        "check_disk_usage": "Check Disk Usage",
        "check_pod_events": "Check Pod Events",
    }
    workflow = DIAGNOSTIC_WORKFLOWS.get(metric, [])
    # Find which workflow steps the agent completed
    matched = []
    for step_id in workflow:
        if step_id in completed_steps:
            matched.append(step_labels.get(step_id, step_id))
    if not matched and completed_steps:
        matched = [step_labels.get(s, s) for s in completed_steps]
    return matched


def _assess_irreversibility(text: str):
    """Check if text contains irreversible action suggestions (upgrade, scale, restart, delete, etc.)."""
    import re
    keywords = r'\b(upgrade|scale\s+(up|down|replicas)|restart|delete|failover|rollback|terminate|kill)\b'
    match = re.search(keywords, text, re.IGNORECASE)
    if match:
        return True, f"Suggestion involves: {match.group()}"
    return False, ""


def run_agent_sync(query: str) -> AgentResponse:
    """Run the agent synchronously in a cached event loop."""
    trace_id = str(uuid.uuid4())
    telemetry.set_trace_id(trace_id)

    prompt = _PROMPT_TEMPLATE.format(
        alert_id=alert_id,
        service=service,
        metric=metric,
        severity=severity,
    )
    agent = make_agent(
        telemetry_session=telemetry,
        system_prompt=prompt,
    )

    async def _inner():
        from alert_chatbot import get_runbook_steps, search_runbooks
        import json as _json

        # Pre-fetch runbook data for post-processing — try direct lookup first
        rb_raw = _json.loads(get_runbook_steps(metric=metric))
        rb_text = rb_raw.get("result", "")
        if not rb_text:
            rb_raw = _json.loads(search_runbooks(query=metric))
            rb_text = rb_raw.get("result", "")
        cmds = _extract_runbook_commands(rb_text)

        # Derive sections from diagnostic workflow, not text scanning
        from alert_chatbot import DIAGNOSTIC_WORKFLOWS
        step_labels = {
            "check_health_endpoint": "Check Health Endpoint",
            "check_latency_metrics": "Check Latency Metrics",
            "check_pod_resources": "Check Pod Resources",
            "assess_options": "Assess Remediation Options",
            "propose_manifest": "Propose Manifest Change",
            "check_error_logs": "Check Error Logs",
            "check_recent_deploy": "Check Recent Deploy",
            "check_pool_metrics": "Check Connection Pool Metrics",
            "check_connection_count": "Check Connection Count",
            "check_disk_usage": "Check Disk Usage",
            "check_pod_events": "Check Pod Events",
        }
        workflow = DIAGNOSTIC_WORKFLOWS.get(metric, [])
        all_sections = [step_labels.get(s, s) for s in workflow]
        context_retrieved = []
        if rb_text:
            from alert_chatbot import RUNBOOK_MAP
            rpath = RUNBOOK_MAP.get(metric, "")
            context_retrieved = [str(rpath)] if rpath else ["runbook_lookup"]

        # Build constraint check from alert context + metric thresholds
        constraint_checks = []
        if metric:
            from alert_chatbot import METRIC_THRESHOLDS
            ck = {"metric": metric, "source": "alert_context"}
            threshold_info = METRIC_THRESHOLDS.get(metric)
            if threshold_info:
                ck["threshold"] = threshold_info["threshold"]
                ck["unit"] = threshold_info["unit"]
            constraint_checks.append(ck)

        # Run the agent
        handler = agent.run(query, max_iterations=MAX_ITERATIONS, early_stopping_method='generate')
        result = await handler
        resp = parse_agent_response(str(result))

        # Log decision trace for this agent run
        telemetry.log_decision_trace(
            intent=f"diagnose_{metric}",
            context_retrieved=context_retrieved,
            constraint_checks=constraint_checks,
            policies_applied=all_sections,
            confidence=1.0,
        )

        # When runbook has structured resolution options, replace the agent's hallucinated
        # response with the actual runbook content (small model can't be trusted).
        resolution = _extract_runbook_resolution(rb_text)
        if resolution and "Option 1" in resolution:
            # Ensure proper markdown: blank line before dash lists
            import re as _re
            formatted = _re.sub(r'(\*\*.*?\*\*)\n(\s*-)', r'\1\n\n\2', resolution)
            resp.reasoning = "Resolution options from runbook:\n\n" + formatted
        else:
            # Only inject diagnostic commands when there are no resolution options
            if cmds and not any(cmd in resp.reasoning for cmd in ["curl ", "kubectl "]):
                resp.reasoning = "Diagnostic commands from runbook:\n" + cmds + "\n\n---\n\n" + resp.reasoning

        # Extract proposed manifest if present
        manifest_yaml = _extract_manifest(resp.reasoning)
        resp.manifest_yaml = manifest_yaml

        # Derive assessment from model's actual output, not the runbook
        irreversible, reason = _assess_irreversibility(resp.reasoning)
        resp.has_irreversible_suggestion = irreversible or bool(manifest_yaml)
        resp.irreversible_reason = reason or (f"Proposed manifest change for {service}" if manifest_yaml else "")
        resp.confidence = 0.8 if rb_text else 0.3

        object.__setattr__(resp, "_cited_runbooks", context_retrieved)
        cited_sections = _derive_cited_sections(metric, telemetry.completed_diagnostic_steps)
        object.__setattr__(resp, "_cited_sections", cited_sections)

        return resp

    loop = _get_loop()
    return loop.run_until_complete(_inner())


def _get_loop():
    if "loop" not in st.session_state:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st.session_state.loop = loop
    return st.session_state.loop


# ── Main panel: Alert Context + Chat ──
alert_context = f"""Alert ID: {alert_id}
Service: {service}
Metric: {metric}
Severity: {severity}
Time: {datetime.now(timezone.utc).isoformat()}Z
Status: firing
Reopen count: {repeat_count}
"""

with st.expander("📡 Alert Context", expanded=True):
    st.code(alert_context, language="text")

default_query = f"Why is {service} having issues? Troubleshoot {metric}."
user_query = st.text_area("💬 Ask the diagnostic agent:", default_query)

col1, col2 = st.columns([1, 5])
with col1:
    submitted = st.button("🔍 Diagnose", type="primary")
with col2:
    st.caption("Agent proposes changes — all mutations require your approval.")

if submitted:
    if not user_query.strip():
        st.warning("Please enter a question.")
    else:
        with st.spinner("Agent is reasoning..."):
            import time as _time
            t0 = _time.perf_counter()
            resp = run_agent_sync(user_query)
            elapsed = (_time.perf_counter() - t0) * 1000

        telemetry.log_interaction(
            user_query=user_query,
            agent_response=resp.reasoning,
            duration_ms=elapsed,
            has_irreversible_suggestion=resp.has_irreversible_suggestion,
            irreversible_reason=resp.irreversible_reason,
            confidence=resp.confidence,
            cited_runbooks=getattr(resp, "_cited_runbooks", []),
            cited_sections=getattr(resp, "_cited_sections", []),
        )

        st.session_state.last_response = resp
        if resp.has_irreversible_suggestion:
            st.session_state.irreversible_pending = True
            st.session_state.approval_done = False
        else:
            st.session_state.irreversible_pending = False

# ── Display results / escalation gate ──
if st.session_state.approval_done:
    resp = st.session_state.last_response
    if resp.manifest_yaml:
        st.success("✅ Change approved. Manifest ready for apply.")
        manifest_path = f"/tmp/{alert_id}_manifest.yaml"
        with open(manifest_path, "w") as f:
            f.write(resp.manifest_yaml)
        st.code(resp.manifest_yaml, language="yaml")
        st.info(f"Manifest written to `{manifest_path}`. Apply with:\n\n```bash\nkubectl apply -f {manifest_path}\n```")
    else:
        st.success("✅ Decision logged. Execute the recommended action manually.")
    st.markdown(resp.reasoning)
    st.session_state.show_feedback = True

elif st.session_state.irreversible_pending and st.session_state.last_response:
    resp = st.session_state.last_response
    st.error(f"⚠️ Irreversible action detected: {resp.irreversible_reason}")
    if resp.manifest_yaml:
        st.markdown("**Review the proposed manifest below. Approve only after verifying the manifest.**")
        st.markdown("**Proposed manifest change:**")
        st.code(resp.manifest_yaml, language="yaml")
        confirmed = st.checkbox("I have reviewed the manifest and approve this change.")
        if st.button("Approve", disabled=not confirmed):
            telemetry.log_approval_requested(
                suggestion_text=resp.reasoning,
                irreversible_reason=resp.irreversible_reason or "",
                confidence=resp.confidence,
                user_confirmed=confirmed,
            )
            st.session_state.approval_done = True
            st.session_state.irreversible_pending = False
            manifest_path = f"/tmp/{alert_id}_manifest.yaml"
            with open(manifest_path, "w") as f:
                f.write(resp.manifest_yaml)
            print(f"[APPROVE] Manifest written to {manifest_path} for {service}")
            st.rerun()
    else:
        st.markdown("**This is an informational advisory. The recommended action is irreversible — evaluate carefully before proceeding.**")
        st.markdown(resp.reasoning)
        st.session_state.show_feedback = True

elif st.session_state.last_response:
    resp = st.session_state.last_response
    elapsed_ms = 0
    st.success("✅ Suggestions - you must execute them manually")

    if resp.confidence < 0.7:
        st.warning(f"⚠️ Low confidence ({resp.confidence:.1f}) – recommend manual verification.")
    st.markdown(resp.reasoning)
    st.session_state.show_feedback = True

# ── Post-resolution feedback form ──
_in_approval_gate = (
    st.session_state.irreversible_pending
    and st.session_state.last_response
    and st.session_state.last_response.manifest_yaml
)
if (
    st.session_state.show_feedback
    and st.session_state.last_response
    and not st.session_state.feedback_submitted
    and not _in_approval_gate
):
    st.divider()
    st.markdown("**📋 Post-resolution feedback**")
    helped = st.radio(
        "Did the agent's suggestion help resolve the issue?",
        options=[None, "Yes", "Partially", "No"],
        format_func=lambda x: "— Select —" if x is None else x,
        index=0,
        key="feedback_helped",
    )
    actual_fix = ""
    if helped and helped != "Yes":
        actual_fix = st.text_area(
            "What did you actually do to fix the issue?",
            key="feedback_actual_fix",
        )
    if st.button("Submit feedback", key="feedback_submit"):
        helped_bool = {"Yes": True, "Partially": True, "No": False}.get(helped) if helped else None
        telemetry.log_resolution_feedback(helped=helped_bool, actual_fix=actual_fix)
        st.session_state.feedback_submitted = True
        st.success("Feedback recorded. Thank you!")
        st.rerun()

# ── Close ticket ──
_show_close = (
    st.session_state.show_feedback
    and st.session_state.last_response
    and not _in_approval_gate
    and not st.session_state.ticket_closed
)
if _show_close:
    st.divider()
    if st.button("✅ Close ticket", type="primary", key="close_ticket"):
        mttr = telemetry.log_ticket_closed()
        st.session_state.ticket_closed = True
        st.session_state.mttr_seconds = mttr
        st.rerun()

if st.session_state.ticket_closed and st.session_state.mttr_seconds is not None:
    mttr = st.session_state.mttr_seconds
    if mttr < 120:
        mttr_str = f"{mttr:.0f}s"
    elif mttr < 7200:
        mttr_str = f"{mttr / 60:.1f}m"
    else:
        mttr_str = f"{mttr / 3600:.1f}h"
    st.info(f"📊 Ticket closed. MTTR: **{mttr_str}** (from alert to close)")

if st.checkbox("Show agent reasoning trace (verbose)"):
    st.text(
        "Verbose output is printed to the server console "
        "(enable verbose=True in ReActAgent)."
    )


