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

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-drift-e2e}"
NAMESPACE="${NAMESPACE:-default}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFESTS_DIR="${REPO_ROOT}/examples/desired"
REMEDIATE="${REMEDIATE:-false}"
KEEP_CLUSTER="${KEEP_CLUSTER:-false}"

# Helpers
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
    python3 -m gitops_drift.main \
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

# Preflight checks
log_step "Checking required tools..."
require_tool kind
require_tool kubectl
require_tool jq
require_tool python3
log_ok "All tools present."

# Step 1: Create or reuse cluster
log_step "Checking for kind cluster '${CLUSTER_NAME}'..."
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    log_ok "Cluster '${CLUSTER_NAME}' already exists, reusing."
else
    log_step "Creating kind cluster '${CLUSTER_NAME}'..."
    kind create cluster --name "${CLUSTER_NAME}" --wait 60s
    log_ok "Cluster created."
fi
kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null

# Step 2: Install the controller
log_step "Installing gitops-drift controller..."
pip install -e "${REPO_ROOT}[dev]" -q
log_ok "Controller installed."

# Step 3: Apply desired manifests and wait for rollout
log_step "Applying desired manifests from ${MANIFESTS_DIR}..."
kubectl apply -f "${MANIFESTS_DIR}/"
kubectl rollout status deployment/demo-app -n "${NAMESPACE}" --timeout=90s
log_ok "Manifests applied and rollout complete."

# Step 4: Baseline - assert no drift
log_step "Running dry-run detection (expect: no drift)..."
BASELINE=$(run_controller --dry-run --once)
BASELINE_COUNT=$(echo "${BASELINE}" | jq '.resources | length')
assert_eq "Baseline drift count" "0" "${BASELINE_COUNT}"

# Step 5: Simulate drift across supported resource types
log_step "Simulating drift: kubectl set image deployment/demo-app demo-app=nginx:1.19..."
kubectl set image deployment/demo-app demo-app=nginx:1.19 -n "${NAMESPACE}"

log_step "Simulating drift: update ConfigMap data..."
kubectl patch configmap demo-app-config -n "${NAMESPACE}" \
    --patch '{"data":{"LOG_LEVEL":"debug","MAX_CONNECTIONS":"200"}}'

log_step "Simulating drift: update Namespace label..."
kubectl label namespace demo env=staging --overwrite

log_step "Simulating drift: update Service label..."
kubectl patch service demo-app -n "${NAMESPACE}" \
    --patch '{"metadata":{"labels":{"app":"demo-app-drift"}}}'

# Brief pause so the API server reflects the change.
sleep 2

# Step 6: Detect drift - assert each changed resource is reported
log_step "Running drift detection (expect: Deployment, ConfigMap, Namespace, and Service drift)..."
DRIFT_REPORT=$(run_controller --dry-run --once)

# Verify revision field is present in the JSON envelope.
REVISION=$(echo "${DRIFT_REPORT}" | jq -r '.revision')
if [ "${REVISION}" = "null" ] || [ -z "${REVISION}" ]; then
    log_fail "JSON envelope missing 'revision' field"
fi
log_ok "Revision present: ${REVISION:0:12}..."

DRIFT_COUNT=$(echo "${DRIFT_REPORT}" | jq '.resources | length')
assert_eq "Drifted resource count" "4" "${DRIFT_COUNT}"

DEPLOYMENT_COUNT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "Deployment" and .name == "demo-app")] | length')
CONFIGMAP_COUNT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "ConfigMap" and .name == "demo-app-config")] | length')
NAMESPACE_COUNT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "Namespace" and .name == "demo")] | length')
SERVICE_COUNT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "Service" and .name == "demo-app")] | length')
assert_eq "Deployment drift entry" "1" "${DEPLOYMENT_COUNT}"
assert_eq "ConfigMap drift entry" "1" "${CONFIGMAP_COUNT}"
assert_eq "Namespace drift entry" "1" "${NAMESPACE_COUNT}"
assert_eq "Service drift entry" "1" "${SERVICE_COUNT}"

# Confirm the changed fields are in the report.
IMAGE_DRIFT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "Deployment" and .name == "demo-app") | .fields[].path] | any(contains("image"))')
assert_eq "Image drift detected" "true" "${IMAGE_DRIFT}"

CONFIG_DRIFT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "ConfigMap" and .name == "demo-app-config") | .fields[].path] | any(. == "data.LOG_LEVEL")')
assert_eq "ConfigMap LOG_LEVEL drift detected" "true" "${CONFIG_DRIFT}"

NAMESPACE_DRIFT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "Namespace" and .name == "demo") | .fields[].path] | any(. == "metadata.labels.env")')
assert_eq "Namespace env label drift detected" "true" "${NAMESPACE_DRIFT}"

SERVICE_DRIFT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "Service" and .name == "demo-app") | .fields[].path] | any(. == "metadata.labels.app")')
assert_eq "Service app label drift detected" "true" "${SERVICE_DRIFT}"

# Confirm spec.replicas is NOT in the report (it is excluded via annotation).
REPLICA_DRIFT=$(echo "${DRIFT_REPORT}" | jq '[.resources[] | select(.kind == "Deployment" and .name == "demo-app") | .fields[].path] | any(contains("replicas"))')
assert_eq "spec.replicas correctly excluded from report" "false" "${REPLICA_DRIFT}"

# Show the full drift report for human review.
echo ""
echo "  Drift report:"
echo "${DRIFT_REPORT}" | jq '.resources[] | .kind + "/" + .name as $resource | .fields[] | "  \($resource) \(.path): \(.desired) -> \(.live)"' -r | sed 's/^/    /'

# Verify --fail-on-drift exits non-zero.
log_step "Verifying --fail-on-drift exits non-zero when drift exists..."
if run_controller --dry-run --once --fail-on-drift >/dev/null 2>&1; then
    log_fail "--fail-on-drift should have exited non-zero but returned 0"
fi
log_ok "--fail-on-drift correctly returned non-zero."

# Step 7: Optional remediation
if [ "${REMEDIATE}" = "true" ]; then
    log_step "Running remediation (--remediate --once)..."
    run_controller --remediate --once

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

    LIVE_LOG_LEVEL=$(kubectl get configmap demo-app-config -n "${NAMESPACE}" \
        -o jsonpath='{.data.LOG_LEVEL}')
    assert_eq "ConfigMap LOG_LEVEL after remediation" "info" "${LIVE_LOG_LEVEL}"

    LIVE_NAMESPACE_ENV=$(kubectl get namespace demo -o jsonpath='{.metadata.labels.env}')
    assert_eq "Namespace env label after remediation" "dev" "${LIVE_NAMESPACE_ENV}"
fi

# Done
echo ""
echo -e "${BOLD}${GREEN}All E2E assertions passed.${NC}"
