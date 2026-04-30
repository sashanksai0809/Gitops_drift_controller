#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="drift-demo"

if ! command -v kind &>/dev/null; then
  echo "kind is not installed; nothing to clean up."
  exit 0
fi

if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
  echo "Deleting kind cluster '${CLUSTER_NAME}'..."
  kind delete cluster --name "${CLUSTER_NAME}"
  echo "Cluster deleted."
else
  echo "Cluster '${CLUSTER_NAME}' does not exist. Nothing to do."
fi
