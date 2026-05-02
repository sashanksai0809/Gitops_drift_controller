# Runbook: GitOps Drift Detection Controller

This document covers everything needed to run the controller locally, simulate drift, and exercise both dry-run and remediation modes.

## Prerequisites

- Python 3.9+
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) (Kubernetes in Docker)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [jq](https://jqlang.github.io/jq/) (used by the kind E2E script)
- Docker

---

## 1. Create a local kind cluster

```bash
./scripts/setup-kind.sh
```

This creates a cluster named `drift-demo` and verifies it is reachable. If you already have a cluster you want to use, skip this step and make sure `kubectl config current-context` points to it.

Manual equivalent:

```bash
kind create cluster --name drift-demo
kubectl cluster-info --context kind-drift-demo
```

---

## 2. Install dependencies

```bash
pip install -e ".[dev]"
```

This installs the `kubernetes` Python client, `PyYAML`, `pytest`, and `pytest-cov`. It also installs the `gitops-drift` CLI entry point.

To verify:

```bash
python -m gitops_drift.main --help
```

---

## 3. Apply the desired manifests

Apply the example manifests to the cluster so there is a baseline to compare against:

```bash
kubectl apply -f examples/desired/
```

Verify the resources are running:

```bash
kubectl get deployments,services -n default
```

Expected output:

```
NAME                       READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/demo-app   2/2     2            2           30s

NAME               TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)   AGE
service/demo-app   ClusterIP   10.96.xxx.xxx   <none>        80/TCP    30s
```

---

## 4. Run dry-run detection (no drift yet)

```bash
python -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --once
```

Expected output:

```
No drift detected.
```

---

## 5. Simulate drift

These commands change the cluster state without touching the Git manifests, simulating what happens when someone makes a manual "hotfix" directly against the cluster.

**Change the container image** (simulates an incident rollback that never made it back to Git):

```bash
kubectl set image deployment/demo-app demo-app=nginx:1.19
```

**Bump resource limits** (simulates a "quick fix" for a memory pressure event):

```bash
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
```

**Verify the cluster is now in a different state than Git:**

```bash
kubectl get deployment demo-app -o jsonpath='{.spec.template.spec.containers[0].image}'
# should print nginx:1.19
```

---

## 6. Run dry-run detection (with drift)

```bash
python -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --once
```

Expected output:

```
Drift Report
  Git revision : <12-char SHA>
============================================================

Deployment/demo-app (ns: default)
  Action : drift-detected (dry-run)
  Fields : 3 drifted
    spec.template.spec.containers[name=demo-app].image
      desired : nginx:1.25
      live    : nginx:1.19
    spec.template.spec.containers[name=demo-app].resources.limits.cpu
      desired : 250m
      live    : 500m
    spec.template.spec.containers[name=demo-app].resources.limits.memory
      desired : 256Mi
      live    : 512Mi

============================================================
Total: 1 resource(s) drifted, 3 field(s) changed
```

Container paths now use `[name=<container-name>]` notation instead of positional `[0]`. This is semantically correct: containers are matched by name, so the path reflects the container you actually care about rather than its position in the list.

Note that `spec.replicas` does NOT appear in the report even though it may differ. The deployment manifest includes `drift.gitops.io/ignore-fields: "spec.replicas"`, which tells the controller to skip that field. This simulates an HPA managing replica counts.

---

## 7. Run remediation mode

Remediation re-applies the desired manifest to the cluster, restoring it to the Git state.

```bash
python -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --remediate \
  --once
```

Expected log output includes:

```
WARNING  root  Remediation mode is ACTIVE -- drift will be corrected automatically
INFO     gitops_drift.remediator  Remediating Deployment/demo-app in namespace 'default' (3 field(s) drifted)
INFO     gitops_drift.kubernetes_client  Updated Deployment/demo-app in namespace 'default'
```

Verify the cluster is back in sync:

```bash
kubectl get deployment demo-app -o jsonpath='{.spec.template.spec.containers[0].image}'
# should print nginx:1.25
```

Run dry-run again to confirm no remaining drift:

```bash
python -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --once
```

---

## 8. Run the continuous loop

Omit `--once` to run in loop mode. The controller reconciles every `--interval` seconds:

```bash
python -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --interval 30
```

Press `Ctrl+C` to stop.

---

## 9. Use the demo script

The demo script automates steps 5 and 6 -- it applies drift and runs detection:

```bash
./scripts/demo-drift.sh
```

---

## 10. Run the tests

```bash
pytest -v
```

Expected output:

```
All tests should pass. Run pytest -v to see the latest test list.
75 passed in 0.XXs
```

Run with coverage:

```bash
pytest --cov=src/gitops_drift --cov-report=term-missing
```

---

## 11. Tear down

```bash
./scripts/cleanup.sh
```

This deletes the kind cluster and removes the Docker network.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'kubernetes'`**
Run `pip install -e ".[dev]"` from the repo root.

**`Could not load Kubernetes config`**
Make sure your kind cluster is running: `kubectl cluster-info`. If you have multiple contexts, check with `kubectl config current-context`.

**Drift report shows many unexpected fields**
The controller only flags fields present in the desired manifest. If you see unexpected fields, check that your manifest is minimal -- avoid copying fields from `kubectl get -o yaml` output without stripping system fields first.

**`ApiException: (409) Conflict` during remediation**
This can happen if the resource was modified between fetch and replace. The error is logged and the loop continues. Re-running will pick up the fresh state.
