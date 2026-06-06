# Runbook: Service Down

## Service: inference-api

### Symptoms
- Endpoint returns 5xx
- Health check failing
- Pods in CrashLoopBackOff
- Zero requests being served

### Diagnostic Steps

1. **Check pod status**
   ```bash
   kubectl get pods -l app=inference-api
   ```

2. **Check pod logs**
   ```bash
   kubectl logs -l app=inference-api --tail=100
   ```

3. **Describe pod for events**
   ```bash
   kubectl describe pods -l app=inference-api
   ```

4. **Check service endpoint**
   ```bash
   kubectl get ep inference-api
   ```

5. **Check deployment rollout status**
   ```bash
   kubectl rollout status deploy/inference-api
   ```

6. **Check resource usage**
   ```bash
   kubectl top pods -l app=inference-api
   ```

### Common Causes
- Out of memory (OOM kill)
- Configuration error
- Missing dependencies
- Image pull failure
- Readiness probe failing

### Resolution
- Rollback to previous version: `kubectl rollout undo deploy/inference-api`
- Update resource limits
- Fix configuration and redeploy
- Restart deployment: `kubectl rollout restart deploy/inference-api`
