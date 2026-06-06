# Runbook: Kubernetes Bug Upgrade Decision

## Service: inference-api (infrastructure)

### Symptoms
- Recurring pod crash loop on inference-api
- CrashBackOffLoop events
- OOMKill on pods under memory pressure
- Pods restarting every few minutes

### Diagnostic Steps

1. **Check pod status**
   ```bash
   kubectl get pods -l app=inference-api
   ```

2. **Check pod logs for OOMKill**
   ```bash
   kubectl logs -l app=inference-api --previous --tail=50
   ```

3. **Check Kubernetes version**
   ```bash
   kubectl version
   ```

4. **Check node conditions**
   ```bash
   kubectl get nodes -o wide
   ```

5. **Check pod resource usage**
   ```bash
   kubectl top pods -l app=inference-api
   ```

### Root Cause
- Pods are hitting a memory accounting bug in Kubernetes 1.26.3
- Fixed in Kubernetes 1.27.x
- Bug triggers specifically under memory pressure patterns from batch inference jobs
- Workaround: cap memory requests on batch jobs (degrades throughput by 15-20%)

### Resolution Options

**Option 1 — Upgrade to Kubernetes 1.27.x**
- Patches the memory accounting bug directly
- Risk: 1.27.x ships with a change to the kubelet eviction manager that behaves differently under high pod density. Clusters running 180+ pods per node may experience instability from the new eviction behavior.
- Tradeoff: may fix the crash loop but introduce a different one

**Option 2 — Hold at 1.26.3 with workaround**
- Avoids upgrade risk
- Workaround: cap memory requests on batch jobs (15-20% throughput degradation)
- Underlying bug remains unresolved
- Tradeoff: guaranteed degraded performance

### Notes
- Neither option is safe — upgrading trades a known problem for a possible one, not upgrading trades a fix for guaranteed degraded performance
- The right decision depends on pod density and business tolerance for throughput degradation
- This is irreversible either way — escalation to infra lead and batch inference team is recommended
