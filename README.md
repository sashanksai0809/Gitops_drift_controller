# GitOps Drift Detection Controller

A focused, production-quality tool that compares Kubernetes manifests stored in Git against live cluster state and reports -- or optionally corrects -- any divergence.

## Why this is not ArgoCD or Flux

ArgoCD and Flux are fully-featured GitOps platforms: they handle multi-cluster syncs, RBAC, SSO, UI dashboards, Helm/Kustomize rendering, and rollback workflows. This tool does one thing -- it detects and reports drift. The scope is intentional. A narrower tool is easier to audit, easier to extend, and easier to run in a read-only advisory role alongside an existing platform that you do not control.

Use this when you want a lightweight second opinion on cluster state, a compliance audit trail, or a simple drift check in a CI pipeline. Do not use this as a replacement for a full GitOps platform in production.

## Features

- Reconciliation loop with configurable interval or single-shot mode
- Structured drift report per resource: kind, name, namespace, field path, desired value, live value, action taken
- Dry-run by default -- never touches the cluster unless `--remediate` is explicitly passed
- Annotation-based field exclusions (`drift.gitops.io/ignore-fields`) for fields that are allowed to drift (e.g. replicas managed by HPA)
- System field normalization strips `resourceVersion`, `uid`, `managedFields`, `status`, and other API-server-injected fields before diffing
- Optional remediation mode that re-applies the desired manifest when drift is found
- Missing resources are reported (and optionally created) in remediation mode
- Safe error handling: a failed API call for one resource logs the error and continues to the next

## Supported resource types

Deployment, Service, ConfigMap, Namespace. See [Assumptions and descoped areas](#assumptions-and-descoped-areas) for context.

## Quick start

```bash
# 1. Install dependencies
pip install -e ".[dev]"

# 2. Start a local cluster (requires kind)
./scripts/setup-kind.sh

# 3. Apply the example desired manifests
kubectl apply -f examples/desired/

# 4. Run dry-run detection (nothing drifted yet)
python -m gitops_drift.main --manifests ./examples/desired --namespace default --dry-run --once

# 5. Simulate drift
kubectl set image deployment/demo-app demo-app=nginx:1.19
kubectl patch deployment demo-app -p '{"spec":{"template":{"spec":{"containers":[{"name":"demo-app","resources":{"limits":{"cpu":"500m","memory":"512Mi"}}}]}}}}'

# 6. Detect drift
python -m gitops_drift.main --manifests ./examples/desired --namespace default --dry-run --once

# 7. Remediate
python -m gitops_drift.main --manifests ./examples/desired --namespace default --remediate --once
```

See [RUNBOOK.md](RUNBOOK.md) for detailed step-by-step instructions including kind cluster setup and expected output.

## CLI reference

```
python -m gitops_drift.main [options]

  --manifests PATH       Directory of desired-state YAML manifests (required)
  --namespace NAME       Default namespace when manifest omits one (default: default)
  --dry-run              Report drift without modifying the cluster (default: on)
  --remediate            Re-apply desired state when drift is found (disables dry-run)
  --once                 Run one reconciliation cycle and exit
  --interval SECONDS     Loop interval in seconds (default: 60)
  --kubeconfig PATH      Path to kubeconfig; defaults to ~/.kube/config
  --ignore-fields PATHS  Comma-separated global field paths to ignore
  --output FORMAT        Report output format: text | json (default: text)
  --log-level LEVEL      DEBUG | INFO | WARNING | ERROR (default: INFO)
```

## Example drift report

```
Drift Report
============================================================

Deployment/demo-app (ns: default)
  Action : drift-detected (dry-run)
  Fields : 2 drifted
    spec.template.spec.containers[0].image
      desired : nginx:1.25
      live    : nginx:1.19
    spec.template.spec.containers[0].resources.limits.cpu
      desired : 250m
      live    : 500m

============================================================
Total: 1 resource(s) drifted, 2 field(s) changed
```

## Exclusion mechanism

Add the annotation `drift.gitops.io/ignore-fields` to any manifest with a comma-separated list of dot-notation field paths. Those fields will be stripped from both the desired and live objects before diffing, so they will never appear in the report and will never be remediated.

```yaml
metadata:
  annotations:
    drift.gitops.io/ignore-fields: "spec.replicas,metadata.labels.env"
```

The example deployment uses this to allow an HPA to manage `spec.replicas` without the controller treating every scale event as drift.

## In-cluster deployment

When running inside Kubernetes, replace the kubeconfig approach with a ServiceAccount:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: drift-controller
  namespace: drift-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: drift-controller
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "create", "update"]
  - apiGroups: [""]
    resources: ["services", "configmaps", "namespaces"]
    verbs: ["get", "list", "create", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: drift-controller
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: drift-controller
subjects:
  - kind: ServiceAccount
    name: drift-controller
    namespace: drift-system
```

The Python client automatically detects in-cluster config when `KUBERNETES_SERVICE_HOST` is set. Remove the `--kubeconfig` flag and the code falls through to `load_incluster_config()`.

## Assumptions and descoped areas

**Resource scope**: Only Deployment, Service, ConfigMap, and Namespace are supported. StatefulSet, DaemonSet, CronJob, Ingress, and CRDs are not. Each additional resource type is straightforward to add in `kubernetes_client.py`, but each can carry subtle edge cases (e.g. StatefulSet update strategies, CRD validation). Keeping the scope narrow makes the tool easier to trust and explain.

**List diffing**: Container lists and port lists are compared element-by-element by position. Semantic matching (e.g. matching containers by `name`) is not implemented. In practice this is fine for the example resources, but it would produce noisy output if container ordering changes between desired and live state.

**No multi-cluster support**: The tool reads a single kubeconfig context. Running across multiple clusters requires running separate instances.

**No history or alerting**: The tool prints to stdout and logs. Integrating with Prometheus, PagerDuty, or Slack is out of scope -- the structured report format is designed to make that integration straightforward.

**No Helm or Kustomize**: Manifests must be plain YAML. Rendering templated formats is a separate concern.

## Interview discussion points

1. **Why custom diff instead of deepdiff?** A recursive diff of ~50 lines is easy to walk through in an interview and has no external dependencies. deepdiff is powerful but adds explanation overhead.

2. **Why full replace instead of strategic merge patch for remediation?** A replace is simpler to reason about and guarantees convergence. The cost is that it can overwrite fields managed by operators -- which is why the exclusion mechanism exists and why remediation is opt-in.

3. **What breaks at scale?** Listing resources one at a time is fine for a handful of manifests. A production controller would use informers and a work queue. The current polling loop is appropriate for the scope.

4. **How would you add StatefulSet support?** Add `StatefulSet` to `SUPPORTED_KINDS`, add `_get`/`_create`/`_replace` branches in `kubernetes_client.py`, and add a test fixture. The diff and normalization logic is resource-agnostic.

5. **Why is dry-run the default?** Because the cost of an unwanted apply is much higher than the cost of one extra flag. Any tool that touches production state should require an explicit opt-in.
