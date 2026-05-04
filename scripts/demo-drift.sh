#!/usr/bin/env bash
# Applies the desired manifests, introduces drift, and runs detection.
# Assumes a running cluster with kubectl configured.
#
# Modes:
#   ./scripts/demo-drift.sh          -- one-shot: baseline check, inject drift, detect once
#   LOOP=1 ./scripts/demo-drift.sh   -- live loop: start controller in background, inject drift
#                                       mid-run, watch it detect on the next cycle
#
# Optional env vars:
#   INTERVAL=15   reconciliation interval in seconds when LOOP=1 (default: 15)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LOOP="${LOOP:-0}"
INTERVAL="${INTERVAL:-15}"

echo "==> Applying desired manifests..."
kubectl apply -f "${REPO_ROOT}/examples/desired/"

echo ""
echo "==> Waiting for deployment to roll out..."
kubectl rollout status deployment/demo-app --timeout=60s

# ---- loop mode ---------------------------------------------------------------
if [[ "${LOOP}" == "1" ]]; then
  LOGFILE="$(mktemp /tmp/drift-controller-XXXXXX.log)"
  echo ""
  echo "==> Starting controller in loop mode (interval=${INTERVAL}s, log: ${LOGFILE})..."
  python3 -m gitops_drift.main \
    --manifests "${REPO_ROOT}/examples/desired" \
    --namespace default \
    --dry-run \
    --interval "${INTERVAL}" >"${LOGFILE}" 2>&1 &
  CONTROLLER_PID=$!
  trap 'kill "${CONTROLLER_PID}" 2>/dev/null || true; echo ""; echo "Controller stopped."' EXIT

  echo "    PID ${CONTROLLER_PID} -- first cycle running..."
  sleep $(( INTERVAL + 3 ))

  echo ""
  echo "==> Injecting drift while the controller is running..."
  "${REPO_ROOT}/scripts/inject-drift.sh"
  echo "    Drift injected. Waiting for the next reconciliation cycle (${INTERVAL}s)..."
  sleep $(( INTERVAL + 3 ))

  echo ""
  echo "==> Stopping controller..."
  kill "${CONTROLLER_PID}" 2>/dev/null || true
  trap - EXIT

  echo ""
  echo "==> Controller output:"
  echo "------------------------------------------------------------"
  cat "${LOGFILE}"
  echo "------------------------------------------------------------"
  rm -f "${LOGFILE}"

  echo ""
  echo "To restore desired state:"
  echo "  python3 -m gitops_drift.main --manifests ./examples/desired --namespace default --remediate --once"
  exit 0
fi

# ---- one-shot mode (default) -------------------------------------------------
echo ""
echo "==> Running baseline detection (should show no drift)..."
python3 -m gitops_drift.main \
  --manifests "${REPO_ROOT}/examples/desired" \
  --namespace default \
  --dry-run \
  --once

echo ""
echo "==> Introducing drift..."
"${REPO_ROOT}/scripts/inject-drift.sh"

echo ""
echo "==> Waiting for deployment to roll out with drifted state..."
kubectl rollout status deployment/demo-app --timeout=60s

echo ""
echo "==> Running drift detection (dry-run)..."
python3 -m gitops_drift.main \
  --manifests "${REPO_ROOT}/examples/desired" \
  --namespace default \
  --dry-run \
  --once

echo ""
echo "Demo complete. Run with --remediate to restore desired state:"
echo "  python3 -m gitops_drift.main --manifests ./examples/desired --namespace default --remediate --once"
