# Runbook: Inference API Error Rate High

## Service: inference-api

### Symptoms
- Error rate > 5%
- Users getting 500 errors
- Model inference failures

### Diagnostic Steps

1. **Check error metrics**
   ```bash
   curl -s http://inference-api:8000/metrics | grep inference_errors_total
   ```

2. **Check recent errors in logs**
   ```bash
   kubectl logs -l app=inference-api --tail=100 | grep ERROR
   ```

3. **Check request rate**
   ```bash
   curl -s http://inference-api:8000/metrics | grep inference_requests_total
   ```

4. **Check for upstream API issues**
   ```bash
   kubectl logs -l app=inference-api --tail=50 | grep "upstream"
   ```

5. **Check model health**
   ```bash
   kubectl exec deploy/inference-api -- curl -s http://localhost:8000/health
   ```

### Common Causes
- Model serving backend failure
- Invalid input payloads
- Rate limiting triggered
- Downstream API timeout
- Resource constraints causing OOM

### Resolution
- Restart deployment
- Check input validation
- Scale up to handle load
- Increase timeout settings
- Failover to secondary model endpoint
