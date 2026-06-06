# Runbook: Kubernetes Node Issues

## Service: inference-api (infrastructure)

### Symptoms
- Pods pending or unschedulable
- Node NotReady
- Disk pressure or memory pressure

### Diagnostic Steps

1. **Check node status**
   ```bash
   kubectl get nodes
   ```

2. **Describe unhealthy node**
   ```bash
   kubectl describe node <node-name>
   ```

3. **Check node conditions**
   ```bash
   kubectl get nodes -o jsonpath='{.items[*].status.conditions}'
   ```

4. **Check pod distribution**
   ```bash
   kubectl get pods -o wide
   ```

5. **Check events**
   ```bash
   kubectl get events --sort-by='.lastTimestamp'
   ```

### Common Causes
- Node resource exhaustion
- Disk full
- Network issues
- Docker/containerd daemon failure

### Resolution
- Drain and cordon node: `kubectl drain <node> --ignore-daemonsets`
- Restart kubelet on node
- Free up disk space
- Add more nodes to cluster
- Move workloads to healthy nodes
