import os
import time
import functools
from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.tools import FunctionTool
from llama_index.core.agent.workflow import ReActAgent
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.core.base.llms.types import (
    ChatMessage, ChatResponse, TextBlock, ThinkingBlock, ToolCallBlock, MessageRole,
)
from llama_index.vector_stores.chroma import ChromaVectorStore


class _QwenOllama(Ollama):
    """Subclass to work around qwen3:8b thinking-field bug.
    qwen3 outputs everything in the `thinking` field with `content` empty.
    This merges thinking into content when content is empty.
    """
    def chat(self, messages, **kwargs):
        result = super().chat(messages, **kwargs)
        return _merge_thinking(result)

    async def achat(self, messages, **kwargs):
        result = await super().achat(messages, **kwargs)
        return _merge_thinking(result)


def _merge_thinking(response: ChatResponse) -> ChatResponse:
    blocks = []
    content = ""
    thinking = None
    for block in response.message.blocks:
        if isinstance(block, TextBlock):
            content = block.text
        elif isinstance(block, ThinkingBlock):
            thinking = block.content
        else:
            blocks.append(block)
    if thinking and not content:
        content = thinking
        thinking = None
    if thinking:
        blocks.append(ThinkingBlock(content=thinking))
    blocks.insert(0, TextBlock(text=content))
    response.message.blocks = blocks
    return response
from chromadb import PersistentClient

from pydantic import BaseModel

INDEX_DIR = "./runbook_index"
COLLECTION_NAME = "sre_runbooks"

RUNBOOK_MAP = {
    "p99_latency": "data/runbooks/high-latency.md",
    "high_latency": "data/runbooks/high-latency.md",
    "InferenceHighLatency": "data/runbooks/high-latency.md",
    "error_rate": "data/runbooks/inference-api-errors.md",
    "InferenceErrorRateHigh": "data/runbooks/inference-api-errors.md",
    "db_pool": "data/runbooks/db-connection-pool.md",
    "db_pool_exhaustion": "data/runbooks/db-connection-pool.md",
    "disk_pressure": "data/runbooks/kubernetes-node-issues.md",
    "crash_loop": "data/runbooks/kubernetes-bug-upgrade.md",
    "service_down": "data/runbooks/service-down.md",
    "unavailable": "data/runbooks/service-down.md",
}

METRIC_THRESHOLDS = {
    "p99_latency": {"threshold": "<500ms p99", "unit": "ms", "source": "runbook"},
    "high_latency": {"threshold": "<500ms p99", "unit": "ms", "source": "runbook"},
    "InferenceHighLatency": {"threshold": "<500ms p99", "unit": "ms", "source": "runbook"},
    "error_rate": {"threshold": "<5%", "unit": "percent", "source": "runbook"},
    "InferenceErrorRateHigh": {"threshold": "<5%", "unit": "percent", "source": "runbook"},
    "db_pool": {"threshold": "<80%", "unit": "percent", "source": "runbook"},
    "db_pool_exhaustion": {"threshold": "<80%", "unit": "percent", "source": "runbook"},
    "disk_pressure": {"threshold": ">20% free", "unit": "percent", "source": "runbook"},
    "crash_loop": {"threshold": "0 restarts in 5m", "unit": "count", "source": "runbook"},
    "service_down": {"threshold": "healthy endpoints == 0", "unit": "count", "source": "runbook"},
    "unavailable": {"threshold": "healthy endpoints == 0", "unit": "count", "source": "runbook"},
}

DIAGNOSTIC_WORKFLOWS = {
    "p99_latency": [
        "check_health_endpoint",
        "check_latency_metrics",
        "check_pod_resources",
        "assess_options",
        "propose_manifest",
    ],
    "high_latency": [
        "check_health_endpoint",
        "check_latency_metrics",
        "check_pod_resources",
        "assess_options",
        "propose_manifest",
    ],
    "InferenceHighLatency": [
        "check_health_endpoint",
        "check_latency_metrics",
        "check_pod_resources",
        "assess_options",
        "propose_manifest",
    ],
    "error_rate": [
        "check_error_logs",
        "check_recent_deploy",
        "assess_options",
        "propose_manifest",
    ],
    "InferenceErrorRateHigh": [
        "check_error_logs",
        "check_recent_deploy",
        "assess_options",
        "propose_manifest",
    ],
    "db_pool": [
        "check_pool_metrics",
        "check_connection_count",
        "assess_options",
        "propose_manifest",
    ],
    "db_pool_exhaustion": [
        "check_pool_metrics",
        "check_connection_count",
        "assess_options",
        "propose_manifest",
    ],
    "disk_pressure": [
        "check_node_disk",
        "check_pod_evictions",
        "assess_options",
    ],
    "crash_loop": [
        "check_oom_logs",
        "check_resource_limits",
        "assess_upgrade_risk",
        "assess_options",
    ],
    "service_down": [
        "check_health_endpoint",
        "check_deployment_status",
        "check_recent_changes",
        "assess_options",
        "propose_manifest",
    ],
    "unavailable": [
        "check_health_endpoint",
        "check_deployment_status",
        "check_recent_changes",
        "assess_options",
        "propose_manifest",
    ],
}


class AgentResponse(BaseModel):
    reasoning: str
    has_irreversible_suggestion: bool = False
    irreversible_reason: str | None = None
    confidence: float = 0.0
    manifest_yaml: str = ""


def _try_parse_json_assessment(text: str) -> AgentResponse | None:
    """Try to parse a JSON assessment block from the response text.

    Looks for ```json ... ``` blocks first, then standalone JSON at the end.
    Expected JSON keys: suggestion, has_irreversible_suggestion, irreversible_reason, confidence.
    """
    import json as _json
    candidates = []

    # 1. Look for ```json ... ``` blocks
    import re as _re
    for match in _re.finditer(r"```json\s*\n?(.*?)\n?```", text, _re.DOTALL):
        candidates.append(match.group(1).strip())
    # 2. Look for standalone { ... } at the end of text
    last_brace = text.rfind("{")
    if last_brace >= 0:
        candidate = text[last_brace:]
        # Try to find matching closing brace
        depth = 0
        for i, ch in enumerate(candidate):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(candidate[: i + 1])
                    break

    for candidate in reversed(candidates):
        try:
            data = _json.loads(candidate)
        except _json.JSONDecodeError:
            continue
        # Skip JSON that doesn't have assessment keys (e.g. assess_options output)
        if not any(k in data for k in ("suggestion", "has_irreversible_suggestion", "irreversible_reason", "confidence", "reasoning")):
            continue
        suggestion = data.get("suggestion", data.get("reasoning", ""))
        has_irreversible = bool(data.get("has_irreversible_suggestion", False))
        reason = data.get("irreversible_reason", None)
        if reason and reason.lower() in ("none", "null", ""):
            reason = None
        confidence = float(data.get("confidence", 0.0))
        return AgentResponse(
            reasoning=suggestion,
            has_irreversible_suggestion=has_irreversible,
            irreversible_reason=reason,
            confidence=min(max(confidence, 0.0), 1.0),
        )
    return None


def parse_agent_response(text: str) -> AgentResponse:
    # Try JSON parsing first (new structured format)
    result = _try_parse_json_assessment(text)
    if result is not None:
        return result

    # Fall back to delimiter-based parsing (existing ASSESSMENT block)
    reasoning = text
    has_irreversible = False
    reason = None
    confidence = 0.0

    delimiters = ["===ASSESSMENT===", "### Assessment:", "**Assessment:**", "Assessment:"]
    delimiter = None
    for d in delimiters:
        if d in text:
            delimiter = d
            break

    if delimiter:
        parts = text.split(delimiter)
        before = parts[0].strip()
        after = parts[1]
        for end_delim in ["===END===", "---"]:
            if end_delim in after:
                after = after.split(end_delim)[0]
                break

        narrative_lines = []
        in_assessment = True
        for line in after.strip().split("\n"):
            stripped = line.strip()
            if in_assessment:
                if stripped.startswith("Irreversible:"):
                    val = stripped.split(":", 1)[1].strip().lower()
                    has_irreversible = val in ("yes", "true", "y")
                elif stripped.startswith("Reason:"):
                    reason = stripped.split(":", 1)[1].strip()
                    if reason.lower() in ("none", "null", ""):
                        reason = None
                elif stripped.startswith("Confidence:"):
                    try:
                        confidence = float(stripped.split(":", 1)[1].strip())
                    except ValueError:
                        confidence = 0.0
                    in_assessment = False
                elif stripped == "":
                    pass
                else:
                    in_assessment = False
                    narrative_lines.append(stripped)
            else:
                narrative_lines.append(stripped)

        if before:
            reasoning = before
        else:
            reasoning = "\n".join(narrative_lines).strip() or ""

    return AgentResponse(
        reasoning=reasoning,
        has_irreversible_suggestion=has_irreversible,
        irreversible_reason=reason,
        confidence=min(max(confidence, 0.0), 1.0),
    )


def _extract(obj, default=""):
    """Extract a plain string from whatever the model passes.

    Small models (3B-8B) often pass the parameter schema (the properties
    dict) as the argument value instead of a simple string. Walk the mess
    to find the actual text.
    """
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, str) and v != "string":
                return v
            if isinstance(v, dict):
                for v2 in v.values():
                    if isinstance(v2, str) and v2 != "string":
                        return v2
    return default


def _normalize_tool_kwargs(kwargs: dict, expected_params: list[str]) -> dict:
    """Normalize model schema-object kwargs into proper parameter dict.

    Small models sometimes wrap params inside a ``kwargs`` key (from the
    ``**kwargs`` function signature), pass the entire JSON schema as args,
    or use wrong parameter names. Detect and fix these issues.
    """
    if "kwargs" in kwargs and isinstance(kwargs["kwargs"], dict):
        kwargs = kwargs["kwargs"]

    if any(p in kwargs for p in expected_params):
        return kwargs

    if "properties" in kwargs and isinstance(kwargs.get("properties"), dict):
        cleaned = {}
        for param_name, param_schema in kwargs["properties"].items():
            if isinstance(param_schema, dict):
                value = param_schema.get(
                    "title", param_schema.get("default", "")
                )
                if not value:
                    value = param_schema.get("description", "")
                cleaned[param_name] = value
            else:
                cleaned[param_name] = param_schema
        return cleaned

    # Last resort: if we have any string values but wrong keys, use the first
    # meaningful value for the first expected parameter (handles `symptom`
    # being used when `query` is expected, etc.)
    if kwargs and expected_params:
        for v in kwargs.values():
            if isinstance(v, str) and v.strip():
                return {expected_params[0]: v.strip()}

    return kwargs


COMMAND_IRREVERSIBILITY_MAP = {
    "restart": (True, "Restarting a deployment disrupts active requests and may cause brief downtime."),
    "reboot": (True, "Rebooting a node terminates all running pods on that node."),
    "delete": (True, "Deleting a resource is destructive and may cause data loss."),
    "failover": (True, "Failing over to a replica may cause a brief service interruption."),
    "rollback": (True, "Rolling back changes may revert critical fixes and cause inconsistency."),
    "scale down": (True, "Scaling down reduces capacity and may drop active connections."),
    "reconfigure": (True, "Reconfiguring a service without validation can cause misconfiguration."),
    "remove": (True, "Removing a component may have cascading effects."),
    "terminate": (True, "Terminating a process or pod kills active work."),
    "kill": (True, "Killing a process may corrupt in-flight state."),
    "latency": (False, ""),
    "error": (False, ""),
    "health": (False, ""),
    "resources": (False, ""),
    "pool": (False, ""),
}


def _check_irreversible(text: str) -> tuple[bool, str]:
    """Scan text for irreversible keywords. Returns (is_irreversible, reason)."""
    lower = text.lower()
    for keyword, (irreversible, reason) in COMMAND_IRREVERSIBILITY_MAP.items():
        if irreversible and keyword in lower:
            return True, reason
    return False, ""


_RUNBOOK_IRREVERSIBLE_PHRASES = [
    "restart", "reboot", "delete", "failover",
    "scale down", "reconfigure", "remove",
    "terminate", "kill",
]


def _keyword_check(text: str) -> tuple[bool, str]:
    """Deterministic keyword scan for irreversible phrases in runbook text."""
    lower = text.lower()
    for phrase in _RUNBOOK_IRREVERSIBLE_PHRASES:
        if phrase in lower:
            return True, f"Triggered by keyword: '{phrase}'"
    return False, ""


def search_runbooks(query: str = "") -> str:
    """CALL SECOND. Semantic search across all runbook content. Use for open-ended questions. Returns JSON with result, irreversible, reason."""
    import json as _json
    query = _extract(query, query)
    if not query:
        return _json.dumps({"result": "No search query provided.", "irreversible": False, "reason": ""})
    index = _load_index()
    retriever = index.as_retriever(similarity_top_k=3)
    nodes = retriever.retrieve(query)
    if not nodes:
        return _json.dumps({"result": "No matching runbook steps found.", "irreversible": False, "reason": ""})
    results = []
    for i, node in enumerate(nodes, 1):
        results.append(f"--- Result {i} (score: {node.score:.3f}) ---\n{node.text}")
    combined = "\n\n".join(results)
    irreversible, reason = _keyword_check(combined)
    return _json.dumps({"result": combined, "irreversible": irreversible, "reason": reason})


def get_runbook_steps(metric: str = "") -> str:
    """CALL FIRST. Direct lookup of runbook steps by alert metric name. Faster and more reliable than semantic search. Returns JSON with result, irreversible, reason."""
    import json as _json
    metric = _extract(metric, metric)
    if not metric:
        return _json.dumps({"result": "No metric provided.", "irreversible": False, "reason": ""})
    filepath = RUNBOOK_MAP.get(metric)
    if not filepath:
        return _json.dumps({"result": "", "irreversible": False, "reason": ""})
    import os as _os
    if not _os.path.exists(filepath):
        return _json.dumps({"result": "", "irreversible": False, "reason": f"Runbook file not found: {filepath}"})
    with open(filepath) as f:
        content = f.read()
    if not content.strip():
        return _json.dumps({"result": "", "irreversible": False, "reason": f"Runbook file is empty: {filepath}"})
    irreversible, reason = _keyword_check(content)
    return _json.dumps({"result": content, "irreversible": irreversible, "reason": reason})


def get_next_step(metric: str = "", completed_steps: str = "") -> str:
    """After calling get_runbook_steps, call this to determine which diagnostic step to perform next. Pass the metric name and a comma-separated list of completed step names. Returns JSON with next_step."""
    import json as _json
    metric = _extract(metric, metric)
    completed_raw = _extract(completed_steps, completed_steps)
    workflow = DIAGNOSTIC_WORKFLOWS.get(metric, [])
    if not workflow:
        return _json.dumps({"next_step": "search_runbooks", "reason": "No workflow defined; fall back to semantic search."})
    completed = [s.strip() for s in completed_raw.split(",") if s.strip()]
    for step in workflow:
        if step not in completed:
            return _json.dumps({"next_step": step})
    return _json.dumps({"next_step": "resolved"})


def suggest_diagnostic_command(service: str = "", symptom: str = "") -> str:
    """CALL SECOND after search_runbooks. Get a specific diagnostic shell command for a given service and symptom keyword (latency, error, health, resources, pool). Returns JSON with command, irreversible, reason."""
    import json as _json
    service = _extract(service, service)
    symptom = _extract(symptom, symptom)
    cmds = {
        "latency": f"curl -s http://{service}:8000/metrics | grep inference_latency",
        "error": f"kubectl logs -l app={service} --tail=50 | grep ERROR",
        "health": f"curl -s http://{service}:8000/health",
        "resources": f"kubectl top pods -l app={service}",
        "pool": f"curl -s http://{service}:8000/metrics | grep db_pool",
        "default": f"curl -s http://{service}:8000/health?verbose=true",
    }
    cmd_symptom = None
    for key in cmds:
        if key in symptom.lower():
            cmd_symptom = key
            break
    if cmd_symptom is None:
        cmd_symptom = "default"
    cmd = cmds.get(cmd_symptom, cmds["default"])
    irreversible, reason = COMMAND_IRREVERSIBILITY_MAP.get(cmd_symptom, (False, ""))
    if not irreversible and reason == "":
        irreversible, reason = _check_irreversible(symptom)
    return _json.dumps({"command": cmd, "irreversible": irreversible, "reason": reason})


def propose_manifest(service: str = "", change_type: str = "", params: str = "") -> str:
    """Generate a K8s manifest YAML for a proposed change. Agent only returns text — UI applies on approval. Returns JSON with manifest, irreversible, reason."""
    import json as _json
    import yaml as _yaml
    service = _extract(service, service)
    change_type = _extract(change_type, change_type)
    params_str = _extract(params, params)
    try:
        params_dict = _json.loads(params_str) if params_str else {}
    except _json.JSONDecodeError:
        params_dict = {}
    if not service:
        return _json.dumps({"manifest": "", "irreversible": False, "reason": "No service provided."})

    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": service, "namespace": "default"},
    }

    if change_type == "scale_replicas":
        replicas = params_dict.get("replicas", 3)
        manifest["spec"] = {"replicas": replicas}
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": service, "namespace": "default"},
            "spec": {"replicas": replicas},
        }
        reason = f"Scale {service} replicas to {replicas}"
        return _json.dumps({"manifest": _yaml.dump(manifest, default_flow_style=False), "irreversible": True, "reason": reason})

    elif change_type == "config_update":
        key = params_dict.get("key", "")
        value = params_dict.get("value", "")
        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"{service}-config", "namespace": "default"},
            "data": {key: str(value)},
        }
        reason = f"Update {service} config: {key}={value}"
        return _json.dumps({"manifest": _yaml.dump(manifest, default_flow_style=False), "irreversible": True, "reason": reason})

    elif change_type == "env_update":
        env_var = params_dict.get("name", "")
        env_val = params_dict.get("value", "")
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": service, "namespace": "default"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{
                            "name": service,
                            "env": [{"name": env_var, "value": env_val}],
                        }]
                    }
                }
            },
        }
        reason = f"Set env {env_var}={env_val} on {service}"
        return _json.dumps({"manifest": _yaml.dump(manifest, default_flow_style=False), "irreversible": True, "reason": reason})

    elif change_type == "rollback":
        revision = params_dict.get("revision", "")
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": service, "namespace": "default"},
            "annotations": {
                "rollback-to": revision if revision else "previous",
                "reason": params_dict.get("reason", "Rollback due to incident"),
            },
        }
        reason = f"Rollback {service} to revision {revision if revision else 'previous'}"
        return _json.dumps({"manifest": _yaml.dump(manifest, default_flow_style=False), "irreversible": True, "reason": reason})

    elif change_type == "resource_limits":
        container = params_dict.get("container", service)
        mem_limit = params_dict.get("memory_limit", "")
        mem_request = params_dict.get("memory_request", "")
        cpu_limit = params_dict.get("cpu_limit", "")
        cpu_request = params_dict.get("cpu_request", "")
        resources = {}
        if mem_limit or cpu_limit:
            resources["limits"] = {}
            if mem_limit:
                resources["limits"]["memory"] = mem_limit
            if cpu_limit:
                resources["limits"]["cpu"] = cpu_limit
        if mem_request or cpu_request:
            resources["requests"] = {}
            if mem_request:
                resources["requests"]["memory"] = mem_request
            if cpu_request:
                resources["requests"]["cpu"] = cpu_request
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": service, "namespace": "default"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{
                            "name": container,
                            "resources": resources,
                        }]
                    }
                }
            },
        }
        reason = f"Set resource limits on {service}/{container}: mem={mem_limit}, cpu={cpu_limit}"
        return _json.dumps({"manifest": _yaml.dump(manifest, default_flow_style=False), "irreversible": False, "reason": reason})

    else:
        reason = f"Unknown change type: {change_type}"
        return _json.dumps({"manifest": "", "irreversible": False, "reason": reason})


def assess_options(service: str = "", situation: str = "") -> str:
    """Call when asked for remediation options or 'what should we do'. Generates structured tradeoff analysis with 2-3 options including reversibility, risk, and tradeoffs. Returns JSON with options array and recommendation."""
    import json as _json
    import re as _re

    service = _extract(service, service)
    situation = _extract(situation, situation)

    if not situation:
        return _json.dumps({
            "options": [{"label": "No situation provided", "reversibility": "N/A", "risk": "N/A", "tradeoffs": "Pass a situation description for analysis.", "manifest_change": False}],
            "recommendation": "Provide a situation description."
        }, indent=2)

    prompt = (
        "You are an SRE advisor. Analyze this situation and suggest 2-3 remediation options.\n\n"
        f"Service: {service}\nSituation: {situation}\n\n"
        "For each option include:\n"
        "- label: short name\n"
        "- reversibility: 'Reversible' or 'Irreversible in effect'\n"
        "- risk: 'Low', 'Medium', or 'High'\n"
        "- tradeoffs: brief tradeoff description\n"
        "- manifest_change: true if this requires a K8s manifest change\n\n"
        "End with a clear recommendation.\n\n"
        "Return ONLY valid JSON. No markdown, no other text.\n"
        '{"options": [{"label": "..", "reversibility": "..", "risk": "..", "tradeoffs": "..", "manifest_change": true}], "recommendation": "..."}'
    )
    response = Settings.llm.complete(prompt)
    text = response.text.strip()

    for match in _re.finditer(r"```(?:json)?\s*\n?(.*?)```", text, _re.DOTALL):
        text = match.group(1).strip()
        break

    try:
        data = _json.loads(text)
        if isinstance(data, dict) and "options" in data and "recommendation" in data:
            return _json.dumps(data, indent=2)
    except _json.JSONDecodeError:
        pass

    return _json.dumps({
        "options": [{"label": "Based on analysis", "reversibility": "Review details", "risk": "Review details", "tradeoffs": text, "manifest_change": False}],
        "recommendation": text
    }, indent=2)


_index_cache = None


def _load_index():
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    chroma_client = PersistentClient(path=INDEX_DIR)
    chroma_collection = chroma_client.get_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en")
    _index_cache = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        embed_model=embed_model,
    )
    return _index_cache


Settings.llm = Ollama(
    model="llama3.1:8b",
    request_timeout=360.0,
    context_window=8192,
    temperature=0.0,
)


TOOL_OWNERS = {
    "get_runbook_steps": "SRE Runbook Team",
    "get_next_step": "SRE Runbook Team",
    "search_runbooks": "SRE Runbook Team",
    "suggest_diagnostic_command": "SRE Engineering",
    "assess_options": "SRE Engineering",
    "propose_manifest": "Platform Team",
}


def _derive_reason_code(tool_name: str, args: dict) -> str:
    """Generate a deterministic reason_code for a tool call based on tool name and args.

    The LLM does not generate this — it is derived entirely from the call
    context so the audit trail always has meaningful decision metadata.
    """
    if tool_name == "get_runbook_steps":
        m = args.get("metric", "")
        return f"lookup_runbook_for_{m}" if m else "lookup_runbook"
    if tool_name == "get_next_step":
        cs = args.get("completed_steps", "")
        if not cs:
            return "query_first_diagnostic_step"
        return "query_next_diagnostic_step"
    if tool_name == "search_runbooks":
        q = args.get("query", "")
        return f"semantic_search_{q[:40]}" if q else "semantic_search"
    if tool_name == "suggest_diagnostic_command":
        sym = args.get("symptom", "")
        return f"diagnostic_command_for_{sym}" if sym else "diagnostic_command"
    if tool_name == "assess_options":
        svc = args.get("service", "")
        return f"assess_remediation_for_{svc}" if svc else "assess_remediation"
    if tool_name == "propose_manifest":
        ct = args.get("change_type", "")
        return f"propose_{ct}_change" if ct else "propose_change"
    return "unknown_reason"


def wrap_tool_with_telemetry(fn, tool_name: str, telemetry_session):
    """Wrap a tool function so every call is logged to the audit trail."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = None
        try:
            result = fn(*args, **kwargs)
            return result
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            combined = {}
            if fn.__code__.co_varnames:
                for i, name in enumerate(fn.__code__.co_varnames[: fn.__code__.co_argcount]):
                    if i < len(args):
                        combined[name] = args[i]
            combined.update(kwargs)
            telemetry_session.log_tool_call(
                tool_name=tool_name,
                owner_team=TOOL_OWNERS.get(tool_name, "Unknown"),
                reason_code=_derive_reason_code(tool_name, combined),
                args=combined,
                result=result if result is not None else "error",
                duration_ms=elapsed,
            )

    return wrapper


def _make_normalized_tool(fn, expected_params):
    """Wrap a tool function to normalize schema-object kwargs before calling."""
    import functools as _ft

    @_ft.wraps(fn)
    def wrapper(**kwargs):
        kwargs = _normalize_tool_kwargs(kwargs, expected_params)
        return fn(**kwargs)

    return wrapper


def make_agent(telemetry_session=None, system_prompt=None):
    """Create a ReActAgent, optionally wiring telemetry into the tools.

    Args:
        telemetry_session: Optional TelemetrySession for audit logging.
        system_prompt: Optional system prompt with alert context embedded.
    """
    fn_direct = _make_normalized_tool(get_runbook_steps, ["metric"])
    fn_next = _make_normalized_tool(get_next_step, ["metric", "completed_steps"])
    fn_runbook = _make_normalized_tool(search_runbooks, ["query"])
    fn_cmd = _make_normalized_tool(
        suggest_diagnostic_command, ["service", "symptom"]
    )
    fn_manifest = _make_normalized_tool(
        propose_manifest, ["service", "change_type", "params"]
    )
    fn_options = _make_normalized_tool(
        assess_options, ["service", "situation"]
    )

    if telemetry_session is not None:
        fn_direct = wrap_tool_with_telemetry(
            fn_direct, "get_runbook_steps", telemetry_session
        )
        fn_next = wrap_tool_with_telemetry(
            fn_next, "get_next_step", telemetry_session
        )
        fn_runbook = wrap_tool_with_telemetry(
            fn_runbook, "search_runbooks", telemetry_session
        )
        fn_cmd = wrap_tool_with_telemetry(
            fn_cmd, "suggest_diagnostic_command", telemetry_session
        )
        fn_manifest = wrap_tool_with_telemetry(
            fn_manifest, "propose_manifest", telemetry_session
        )
        fn_options = wrap_tool_with_telemetry(
            fn_options, "assess_options", telemetry_session
        )

    direct_tool = FunctionTool.from_defaults(fn=fn_direct)
    next_tool = FunctionTool.from_defaults(fn=fn_next)
    runbook_tool = FunctionTool.from_defaults(fn=fn_runbook)
    command_tool = FunctionTool.from_defaults(fn=fn_cmd)
    manifest_tool = FunctionTool.from_defaults(fn=fn_manifest)
    options_tool = FunctionTool.from_defaults(fn=fn_options)

    kwargs = {}
    if system_prompt:
        kwargs["system_prompt"] = system_prompt

    return ReActAgent(
        tools=[direct_tool, next_tool, runbook_tool, command_tool, manifest_tool, options_tool],
        llm=Settings.llm,
        verbose=True,
        **kwargs,
    )


# Default agent (no telemetry, for CLI / quick testing)
agent = make_agent(telemetry_session=None)
