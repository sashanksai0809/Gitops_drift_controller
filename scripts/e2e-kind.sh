#!/usr/bin/env bash
# End-to-end test for the GitOps Drift Detection Controller against a local
# kind cluster. Exercises: baseline (no drift), drift detection (JSON output),
# and optionally remediation.
#
# Usage:
#   ./scripts/e2e-kind.sh              # dry-run detection only
#   REMEDIATE=true ./scripts/e2e-kind.sh  # also test remediation
#   KEEP_CLUSTER=true ./scripts/e2e-kind.sh  # do not delete cluster on exit
#
# Requirements: kind, kubectl, jq, Python 3.9+

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CLUSTER_NAME="${CLUSTER_NAME:-drift-e2e}"
NAMESPACE="${NAMESPACE:-default}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFESTS_DIR="${REPO_ROOT}/examples/desired"
REMEDIATE="${REMEDIATE:-false}"
KEEP_CLUSTER="${KEEP_CLUSTER:-false}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

log_step() { echo -e "\n${BOLD}${YELLOW}▶ $*${NC}"; }
log_ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
log_fail() { echo -e "  ${RED}✗ FAIL:${NC} $*"; exit 1; }

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        log_ok "${label}: ${actual}"
    else
        log_fail "${label}: expected '${expected}', got '${actual}'"
    fi
}

require_tool() {
    command -v "$1" >/dev/null 2>&1 || log_fail "'$1' is required but not installed."
}

run_controller() {
    python -m gitops_drift.main \
        --manifests "${MANIFESTS_DIR}" \
        --namespace "${NAMESPACE}" \
        --output json \
        "$@" 2>/dev/null
}

cleanup() {
    if [ "${KEEP_CLUSTER}" = "false" ]; then
        log_step "Tearing down kind cluster '${CLUSTER_NAME}'..."
        kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
        log_ok "Cluster deleted."
    else
        log_ok "Cluster '${CLUSTER_NAME}' retained (KEEP_CLUSTER=true)."
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
log_step "Checking required tools..."
require_tool kind
require_tool kubectl
require_tool jq
require_tool python
log_ok "All tools present."

# ---------------------------------------------------------------------------
# Step 1: Create or reuse cluster
# ---------------------------------------------------------------------------
log_step "Checking for kind cluster '${CLUSTER_NAME}'..."
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    log_ok "Cluster '${CLUSTER_NAME}' already exists, reusing."
else
    log_step "Creating kind cluster '${CLUSTER_NAME}'..."
    kind create cluster --name "${CLUSTER_NAME}" --wait 60s
    log_ok "Cluster created."
fi
kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null

# ---------------------------------------------------------------------------
# Step 2: Install the controller
# ---------------------------------------------------------------------------
log_step "Installing gitops-drift controller..."
pip install -e "${REPO_ROOT}[dev]" -q
log_ok "Controller installed."

# ---------------------------------------------------------------------------
# Step 3: Apply desired manifests and wait for rollout
# ---------------------------------------------------------------------------
log_step "Applying desired manifests from ${MANIFESTS_DIR}..."
kubectl apply -f "${MANIFESTS_DIR}/"
kubectl rollout status deployment/demo-app -n "${NAMESPACE}" --timeout=90s
log_ok "Manifests applied and rollout complete."

# ---------------------------------------------------------------------------
# Step 4: Baseline — assert no drift
# ---------------------------------------------------------------------------
log_step "Running dry-run detection (expect: no drift)..."
BASELINE=$(run_controller --dry-run --once)
BASELINE_COUNT=$(echo "${BASELINE}" | jq '.resources | length')
assert_eq "Baseline drift count" "0" "${BASELINE_COUNT}"

# ---------------------------------------------------------------------------
# Step 5: Simulate drift — change container image
# ---------------------------------------------------------------------------
log_step "Simulating drift: kubectl set image deployment/demo-app demo-app=nginx:1.19..."
kubectl set image deployment/demo-app demo-app=nginx:1.19 -n "${NAMESPACE}"

# Brief pause so the API server reflects the change.
sleep 2

# ---------------------------------------------------------------------------
# Step 6: Detect drift — assert image field is reported
# ---------------------------------------------------------------------------
log_step "Running drift detection (expect: image drift on Deployment/demo-app)..."
DRIFT_REPORT=$(run_controller --dry-run --once)

# Verify revision field is present in the JSON envelope.
REVISION=$(echo "${DRIFT_REPORT}" | jq -r '.revision')
if [ "${REVISION}" = "null" ] || [ -z "${REVISION}" ]; then
    log_fail "JSON envelope missing 'revision' field"
fi
log_ok "Revision present: ${REVISION:0:12}..."

DRIFT_COUNT=$(echo "${DRIFT_REPORT}" | jq '.resources | length')
assert_eq "Drifted resource count" "1" "${DRIFT_COUNT}"

DRIFTED_KIND=$(echo "${DRIFT_REPORT}" | jq -r '.resources[0].kind')
DRIFTED_NAME=$(echo "${DRIFT_REPORT}" | jq -r '.resources[0].name')
assert_eq "Drifted kind" "Deployment" "${DRIFTED_KIND}"
assert_eq "Drifted name" "demo-app" "${DRIFTED_NAME}"

# Confirm the image field is in the report.
IMAGE_DRIFT=$(echo "${DRIFT_REPORT}" | jq '[.resources[0].fields[].path] | any(contains("image"))')
assert_eq "Image drift detected" "true" "${IMAGE_DRIFT}"

# Confirm spec.replicas is NOT in the report (it is excluded via annotation).
REPLICA_DRIFT=$(echo "${DRIFT_REPORT}" | jq '[.resources[0].fields[].path] | any(contains("replicas"))')
assert_eq "spec.replicas correctly excluded from report" "false" "${REPLICA_DRIFT}"

# Show the full drift report for human review.
echo ""
echo "  Drift report:"
echo "${DRIFT_REPORT}" | jq '.resources[0].fields[] | "  \(.path): \(.desired) → \(.live)"' -r | sed 's/^/    /'

# Verify --fail-on-drift exits non-zero.
log_step "Verifying --fail-on-drift exits non-zero when drift exists..."
if run_controller --dry-run --once --fail-on-drift >/dev/null 2>&1; then
    log_fail "--fail-on-drift should have exited non-zero but returned 0"
fi
log_ok "--fail-on-drift correctly returned non-zero."

# ---------------------------------------------------------------------------
# Step 7: Optional remediation
# ---------------------------------------------------------------------------
if [ "${REMEDIATE}" = "true" ]; then
    log_step "Running remediation (--remediate --once)..."
    run_controller --no-dry-run --remediate --once

    # Give Kubernetes a moment to apply the change.
    sleep 3
    kubectl rollout status deployment/demo-app -n "${NAMESPACE}" --timeout=60s

    log_step "Verifying post-remediation: expect no drift..."
    POST_REPORT=$(run_controller --dry-run --once)
    POST_COUNT=$(echo "${POST_REPORT}" | jq '.resources | length')
    assert_eq "Post-remediation drift count" "0" "${POST_COUNT}"

    LIVE_IMAGE=$(kubectl get deployment demo-app -n "${NAMESPACE}" \
        -o jsonpath='{.spec.template.spec.containers[0].image}')
    assert_eq "Live image after remediation" "nginx:1.25" "${LIVE_IMAGE}"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${GREEN}All E2E assertions passed.${NC}"
