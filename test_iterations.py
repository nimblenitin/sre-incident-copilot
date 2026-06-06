"""Test MAX_ITERATIONS=5 across all 3 scenarios in a single event loop."""
import sys, os, time, json, asyncio

sys.path.insert(0, '.')
os.environ['TERM'] = 'xterm'

from alert_chatbot import make_agent, parse_agent_response

async def main():
    # ═══════════════ SCENARIO 1: Upgrade Decision ═══════════════
    print("=== SCENARIO 1: Upgrade Decision (crash_loop) ===")
    prompt1 = '''SRE agent. Never run commands yourself.

Tools:
- search_runbooks(query) - look up the alert metric in runbooks
- suggest_diagnostic_command(service, symptom) - get a diagnostic shell command
- propose_manifest(service, change_type, params) - propose a K8s manifest change

Call search_runbooks with the metric name. Present the runbook findings directly. Never apply changes directly.

Alert: test-001 | Service: inference-api | Metric: crash_loop | Severity: critical'''

    t0 = time.perf_counter()
    agent1 = make_agent(telemetry_session=None, system_prompt=prompt1)
    handler1 = agent1.run('inference-api is crash looping - pods keep getting OOMKilled.', max_iterations=5, early_stopping_method='generate')
    result1 = await handler1
    e1 = time.perf_counter() - t0
    raw1 = str(result1)
    tc1 = raw1.count('ToolCall(')
    has_upgrade = 'Upgrade' in raw1
    print(f"  {e1:.1f}s | calls={tc1} | resp_len={len(str(result1.response))} | upgrade_ref={has_upgrade}")
    print(f"  SNIPPET: {str(result1.response)[:200]}...")

    # ═══════════════ SCENARIO 2: Manifest Proposal ═══════════════
    print()
    print("=== SCENARIO 2: Manifest Proposal (db_pool) ===")
    prompt2 = '''SRE agent. Never run commands yourself.

Tools:
- search_runbooks(query) - look up the alert metric in runbooks
- propose_manifest(service, change_type, params) - propose a K8s manifest change

Call search_runbooks with the metric name. If the solution involves a config change, call propose_manifest. Never apply changes directly.

Alert: test-001 | Service: inference-api | Metric: db_pool_exhaustion | Severity: critical'''

    t0 = time.perf_counter()
    agent2 = make_agent(telemetry_session=None, system_prompt=prompt2)
    handler2 = agent2.run('db_pool_exhaustion on inference-api. Propose a config update to increase DB_POOL_SIZE to 50.', max_iterations=5, early_stopping_method='generate')
    result2 = await handler2
    e2 = time.perf_counter() - t0
    raw2 = str(result2)
    tc2 = raw2.count('ToolCall(')
    has_manifest = 'DB_POOL_SIZE' in raw2 or 'ConfigMap' in raw2
    print(f"  {e2:.1f}s | calls={tc2} | resp_len={len(str(result2.response))} | manifest={has_manifest}")
    print(f"  SNIPPET: {str(result2.response)[:200]}...")

    # ═══════════════ SCENARIO 3: Diagnostic Commands ═══════════════
    print()
    print("=== SCENARIO 3: Diagnostic Commands (disk_pressure) ===")
    prompt3 = '''SRE agent. Never run commands yourself.

Tools:
- search_runbooks(query) - look up the alert metric in runbooks
- suggest_diagnostic_command(service, symptom) - get a diagnostic shell command
- propose_manifest(service, change_type, params) - propose a K8s manifest change

Call search_runbooks with the metric name. Then call suggest_diagnostic_command to get diagnostic steps. Present the runbook findings and diagnostic commands directly.

Alert: test-001 | Service: node-exporter | Metric: disk_pressure | Severity: warning'''

    t0 = time.perf_counter()
    agent3 = make_agent(telemetry_session=None, system_prompt=prompt3)
    handler3 = agent3.run('Node disk_pressure on node worker-1. Disk usage is at 92%.', max_iterations=5, early_stopping_method='generate')
    result3 = await handler3
    e3 = time.perf_counter() - t0
    raw3 = str(result3)
    tc3 = raw3.count('ToolCall(')
    has_diag = 'df -h' in raw3 or 'diagnostic' in str(result3.response).lower()
    print(f"  {e3:.1f}s | calls={tc3} | resp_len={len(str(result3.response))} | diagnostic={has_diag}")
    print(f"  SNIPPET: {str(result3.response)[:200]}...")

    print()
    print(f"SUMMARY:")
    print(f"1. Upgrade Decision: {e1:.1f}s, {tc1} calls, upgrade_ref={has_upgrade}")
    print(f"2. Manifest Proposal: {e2:.1f}s, {tc2} calls, manifest={has_manifest}")
    print(f"3. Diagnostic: {e3:.1f}s, {tc3} calls, diagnostic={has_diag}")

if __name__ == '__main__':
    asyncio.run(main())
