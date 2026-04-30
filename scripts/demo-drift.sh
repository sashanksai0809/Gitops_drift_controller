#!/usr/bin/env bash
# Applies the desired manifests, introduces drift, and runs detection.
# Assumes a running cluster with kubectl configured.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Applying desired manifests..."
kubectl apply -f "${REPO_ROOT}/examples/desired/"

echo ""
echo "==> Waiting for deployment to roll out..."
kubectl rollout status deployment/demo-app --timeout=60s

echo ""
echo "==> Running baseline detection (should show no drift)..."
python -m gitops_drift.main \
  --manifests "${REPO_ROOT}/examples/desired" \
  --namespace default \
  --dry-run \
  --once

echo ""
echo "==> Introducing drift: changing image to nginx:1.19..."
kubectl set image deployment/demo-app demo-app=nginx:1.19

echo ""
echo "==> Introducing drift: bumping resource limits..."
kubectl patch deployment demo-app --patch '
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

echo ""
echo "==> Waiting for deployment to roll out with drifted state..."
kubectl rollout status deployment/demo-app --timeout=60s

echo ""
echo "==> Running drift detection (dry-run)..."
python -m gitops_drift.main \
  --manifests "${REPO_ROOT}/examples/desired" \
  --namespace default \
  --dry-run \
  --once

echo ""
echo "Demo complete. Run with --remediate to restore desired state:"
echo "  python -m gitops_drift.main --manifests ./examples/desired --namespace default --remediate --once"
