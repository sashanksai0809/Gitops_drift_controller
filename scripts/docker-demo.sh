#!/usr/bin/env bash
# Automates RUNBOOK steps 1-4 using Docker.
# Requirements: kind, docker. No Python or kubectl needed on the host.
#
# Usage:
#   ./scripts/docker-demo.sh                    # run and tear down cluster on exit
#   KEEP_CLUSTER=true ./scripts/docker-demo.sh  # keep the cluster after the run
#
# Note: Designed for Docker Desktop on Mac. On Linux, set DOCKER_HOST_ADDR to
# the Docker bridge gateway (usually 172.17.0.1) or use --network host instead.

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-drift-demo}"
IMAGE="${IMAGE:-gitops-drift:demo}"
KEEP_CLUSTER="${KEEP_CLUSTER:-false}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFESTS_DIR="${REPO_ROOT}/examples/desired"

# On Docker Desktop for Mac, host.docker.internal reaches the host machine.
# kind's API server binds to 127.0.0.1 on the host, so we rewrite the kubeconfig.
DOCKER_HOST_ADDR="${DOCKER_HOST_ADDR:-host.docker.internal}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

log_step() { echo -e "\n${BOLD}${YELLOW}▶ $*${NC}"; }
log_ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
log_fail() { echo -e "  ${RED}✗ FAIL:${NC} $*"; exit 1; }

require_tool() {
    command -v "$1" >/dev/null 2>&1 || log_fail "'$1' is required but not installed."
}

KUBECONFIG_TMP=$(mktemp)
cleanup() {
    rm -f "${KUBECONFIG_TMP}"
    if [ "${KEEP_CLUSTER}" = "false" ]; then
        log_step "Tearing down kind cluster '${CLUSTER_NAME}'..."
        kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
        log_ok "Cluster deleted."
    else
        log_ok "Cluster '${CLUSTER_NAME}' retained (KEEP_CLUSTER=true)."
    fi
}
trap cleanup EXIT

# Preflight
log_step "Checking required tools..."
require_tool kind
require_tool docker
log_ok "kind and docker present."

# Step 1: Create or reuse kind cluster
log_step "Checking for kind cluster '${CLUSTER_NAME}'..."
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    log_ok "Cluster '${CLUSTER_NAME}' already exists, reusing."
else
    log_step "Creating kind cluster '${CLUSTER_NAME}'..."
    kind create cluster --name "${CLUSTER_NAME}" --wait 60s
    log_ok "Cluster created."
fi

# kind's kubeconfig uses 127.0.0.1, which is the host's loopback.
# Containers cannot reach that address directly -- rewrite it to the host alias.
kind get kubeconfig --name "${CLUSTER_NAME}" \
    | sed "s/127\\.0\\.0\\.1/${DOCKER_HOST_ADDR}/g" \
    > "${KUBECONFIG_TMP}"
log_ok "Kubeconfig ready (server: ${DOCKER_HOST_ADDR})."

# Step 2: Build the controller image
log_step "Building Docker image '${IMAGE}'..."
docker build -q -t "${IMAGE}" "${REPO_ROOT}"
log_ok "Image '${IMAGE}' built."

# Step 3: Apply desired manifests using the built image (no host kubectl needed)
log_step "Applying desired manifests from ${MANIFESTS_DIR}..."
docker run --rm \
    -v "${KUBECONFIG_TMP}:/root/.kube/config:ro" \
    -v "${MANIFESTS_DIR}:/manifests:ro" \
    --entrypoint kubectl \
    "${IMAGE}" apply -f /manifests/

log_step "Waiting for deployment/demo-app to be ready..."
docker run --rm \
    -v "${KUBECONFIG_TMP}:/root/.kube/config:ro" \
    --entrypoint kubectl \
    "${IMAGE}" rollout status deployment/demo-app -n default --timeout=90s
log_ok "Manifests applied and rollout complete."

# Step 4: Dry-run detection -- expect no drift
log_step "Running drift detection (expect: no drift)..."
docker run --rm \
    -v "${KUBECONFIG_TMP}:/root/.kube/config:ro" \
    -v "${MANIFESTS_DIR}:/manifests:ro" \
    "${IMAGE}" \
    --manifests /manifests --namespace default --dry-run --once

echo ""
echo -e "${BOLD}${GREEN}Steps 1-4 complete.${NC}"
echo ""
echo "  To continue with drift injection (RUNBOOK step 5), re-run with:"
echo "    KEEP_CLUSTER=true ./scripts/docker-demo.sh"
echo "  Once that finishes, use your local kubectl against context kind-${CLUSTER_NAME}."
