#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="drift-demo"

# Check dependencies
if ! command -v kind &>/dev/null; then
  echo "ERROR: kind is not installed. See https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
  exit 1
fi

if ! command -v kubectl &>/dev/null; then
  echo "ERROR: kubectl is not installed. See https://kubernetes.io/docs/tasks/tools/"
  exit 1
fi

# Check if the cluster already exists
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
  echo "Cluster '${CLUSTER_NAME}' already exists. Skipping creation."
else
  echo "Creating kind cluster '${CLUSTER_NAME}'..."
  kind create cluster --name "${CLUSTER_NAME}"
fi

echo "Waiting for cluster to be ready..."
kubectl wait --for=condition=Ready node --all --timeout=60s --context "kind-${CLUSTER_NAME}"

echo ""
echo "Cluster '${CLUSTER_NAME}' is ready."
echo "Current context: $(kubectl config current-context)"
