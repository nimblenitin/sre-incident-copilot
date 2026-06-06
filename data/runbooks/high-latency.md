# Runbook: High Inference Latency

## Service: inference-api

### Symptoms
- p99 latency > 2s
- Users report slow responses
- Increased timeout errors

### Diagnostic Steps

1. **Check inference API health**
   ```bash
   curl -s http://inference-api:8000/health
   ```

2. **Check current latency metrics**
   ```bash
   curl -s http://inference-api:8000/metrics | grep inference_latency
   ```

3. **Check in-flight requests**
   ```bash
   curl -s http://inference-api:8000/metrics | grep inference_requests_in_flight
   ```

4. **Check pod resource usage**
   ```bash
   kubectl top pods -l app=inference-api
   ```

5. **Check pod logs for errors**
   ```bash
   kubectl logs -l app=inference-api --tail=50
   ```

6. **Verify model is loaded correctly**
   ```bash
   kubectl exec deploy/inference-api -- curl -s http://localhost:8000/health
   ```

### Common Causes
- Traffic spike overwhelming the pod
- Model not cached / cold start
- Resource constraints (CPU/memory)
- Downstream dependency slow

### Resolution
- Scale up replicas: `kubectl scale deploy/inference-api --replicas=3`
- Increase resource limits
- Restart pod if stuck
