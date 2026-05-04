#!/usr/bin/env bash
# Injects the same drift used in the runbook without changing Git manifests.
# Assumes a running cluster with the desired manifests already applied.

set -euo pipefail

NAMESPACE="${NAMESPACE:-default}"

echo "==> Deployment: changing image to nginx:1.19..."
kubectl set image deployment/demo-app demo-app=nginx:1.19 -n "${NAMESPACE}"

echo "==> Deployment: bumping resource limits..."
kubectl patch deployment demo-app -n "${NAMESPACE}" --patch '
spec:
  template:
    spec:
      containers:
      - name: demo-app
        resources:
          limits:
            cpu: "500m"
            memory: "512Mi"
'

echo "==> ConfigMap: changing config values..."
kubectl patch configmap demo-app-config -n "${NAMESPACE}" \
  --patch '{"data":{"LOG_LEVEL":"debug","MAX_CONNECTIONS":"200"}}'

echo "==> Service: changing metadata label..."
kubectl patch service demo-app -n "${NAMESPACE}" \
  --patch '{"metadata":{"labels":{"app":"demo-app-drift"}}}'

echo "==> Namespace: changing env label..."
kubectl patch namespace demo \
  --patch '{"metadata":{"labels":{"env":"staging"}}}'

echo ""
echo "Drift injected. Run dry-run detection next:"
echo "  python3 -m gitops_drift.main --manifests ./examples/desired --namespace ${NAMESPACE} --dry-run --once"
