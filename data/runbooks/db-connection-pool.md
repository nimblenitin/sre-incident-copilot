# Runbook: Database Connection Pool Exhaustion

## Service: inference-api

### Symptoms
- "connection pool exhausted" errors
- Timeouts on database queries
- Increased error rate

### Diagnostic Steps

1. **Check database connection pool metrics**
   ```bash
   curl -s http://inference-api:8000/metrics | grep db_pool
   ```

2. **Check active connections**
   ```bash
   kubectl exec deploy/inference-api -- curl -s http://localhost:8000/debug/pool-stats
   ```

3. **Check for slow queries**
   ```bash
   kubectl logs -l app=inference-api --tail=100 | grep "slow query"
   ```

4. **Check database pod resource usage**
   ```bash
   kubectl top pods -l app=postgres
   ```

### Common Causes
- Connection leak in application code
- Too many concurrent requests
- Database server resource exhaustion
- Network latency between app and database

### Resolution
- Increase pool size in config
- Restart connection pool
- Scale database read replicas
- Identify and fix connection leak
