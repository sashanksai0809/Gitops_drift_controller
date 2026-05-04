# GitOps Drift Detection Controller: Runbook

Quick reference for running the controller locally, injecting drift, and testing both dry-run and remediation.

## Prerequisites

- Python 3.9+
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) or [k3d](https://k3d.io/#installation)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [jq](https://jqlang.github.io/jq/)
- [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) (needs to be running before anything else)

```bash
brew install python@3.11 kind kubectl jq
```

Docker Desktop has to be installed separately. Start it before moving on and do a quick sanity check:

```bash
docker info
```

---

## Optional shortcut: Docker demo for steps 1-4

If you have Docker and kind installed, `scripts/docker-demo.sh` can run the setup and first dry-run without installing Python dependencies locally:

```bash
./scripts/docker-demo.sh
```

The script creates a kind cluster on the host, builds the controller image (which packages the controller and kubectl), applies the example manifests using that image, and runs a dry-run detection. It covers steps 1-4 only.

To continue from step 5 (inject drift) after the script finishes, keep the cluster alive:

```bash
KEEP_CLUSTER=true ./scripts/docker-demo.sh
```

Then pick up from step 5 below using your local kubectl against `kind-drift-demo`.

> **Linux note:** Docker Desktop is Mac/Windows only. On Linux, set `DOCKER_HOST_ADDR=172.17.0.1` (the Docker bridge gateway) before running the script.

---

## 1. Create a local cluster (kind or k3s)

### kind

```bash
./scripts/setup-kind.sh
```

Spins up a cluster called `drift-demo` and checks it's reachable. If you already have a cluster you want to use, skip this and make sure `kubectl config current-context` is pointing at the right one.

To create it by hand:

```bash
kind create cluster --name drift-demo
kubectl cluster-info --context kind-drift-demo
```

### k3s (via k3d)

k3d runs k3s clusters inside Docker. The controller works against k3s the same way it does against kind -- the API surface it uses (`get`, `create`, `update` on Deployments, Services, ConfigMaps, Namespaces) is standard across both.

```bash
brew install k3d
k3d cluster create drift-demo
kubectl config use-context k3d-drift-demo
```

For steps 2 onwards, use the same commands. The only difference is the context name prefix (`k3d-` instead of `kind-`).

To tear down a k3d cluster when you're done:

```bash
k3d cluster delete drift-demo
```

---

## 2. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Keeps dependencies isolated from your system Python. In a new terminal, run source .venv/bin/activate from the repo root.

Sanity check:

```bash
python3 -m gitops_drift.main --help
```

---

## 3. Apply the desired manifests

```bash
kubectl apply -f examples/desired/
```

Check the namespaced resources:

```bash
kubectl get deployments,services,configmaps -n default
```

```
NAME                       READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/demo-app   2/2     2            2           30s

NAME               TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)   AGE
service/demo-app   ClusterIP   10.96.xxx.xxx   <none>        80/TCP    30s

NAME                      DATA   AGE
configmap/demo-app-config 3      30s
```

Check the cluster-scoped Namespace separately (Namespaces don't belong to a namespace, so `-n default` doesn't apply):

```bash
kubectl get namespace demo
```

```
NAME   STATUS   AGE
demo   Active   30s
```

---

## 4. Dry-run with no drift

```bash
python3 -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --once
```

```
No drift detected.
  Git revision : <12-char SHA>
```

---

## 5. Inject drift

Make changes directly in the cluster, simulating what happens after an incident hotfix or a manual `kubectl edit` that never gets pushed back to Git.

Run all drift changes at once:

```bash
./scripts/inject-drift.sh
```

Or run the individual changes by hand:

**Deployment: change the image**

```bash
kubectl set image deployment/demo-app demo-app=nginx:1.19
```

**Deployment: bump resource limits**

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

**ConfigMap: change a config value (optional, shows multi-resource detection)**

```bash
kubectl patch configmap demo-app-config --patch '{"data":{"LOG_LEVEL":"debug","MAX_CONNECTIONS":"200"}}'
```

**Service: change a metadata label (optional, shows Service drift without breaking routing)**

```bash
kubectl patch service demo-app --patch '{"metadata":{"labels":{"app":"demo-app-drift"}}}'
```

**Namespace: change a label (optional, shows cluster-scoped resource detection)**

```bash
kubectl patch namespace demo --patch '{"metadata":{"labels":{"env":"staging"}}}'
```

Confirm the Deployment diverged from Git:

```bash
kubectl get deployment demo-app -o jsonpath='{.spec.template.spec.containers[0].image}'
# nginx:1.19
```

---

## 6. Dry-run with drift

```bash
python3 -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --once
```

If you ran the ConfigMap, Service, and Namespace patches in step 5, you'll see all four resources in the report:

```
Drift Report
  Git revision : <12-char SHA>
============================================================

ConfigMap/demo-app-config (ns: default)
  Action : drift-detected (dry-run)
  Fields : 2 drifted
    data.LOG_LEVEL
      desired : info
      live    : debug
    data.MAX_CONNECTIONS
      desired : 100
      live    : 200

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

Namespace/demo (ns: )
  Action : drift-detected (dry-run)
  Fields : 1 drifted
    metadata.labels.env
      desired : dev
      live    : staging

Service/demo-app (ns: default)
  Action : drift-detected (dry-run)
  Fields : 1 drifted
    metadata.labels.app
      desired : demo-app
      live    : demo-app-drift

============================================================
Total: 4 resource(s) drifted, 7 field(s) changed
```

The Namespace entry shows `ns: ` (empty) because Namespaces are cluster-scoped; they don't belong to a namespace.

If you only ran the Deployment patches, the ConfigMap, Service, and Namespace blocks won't appear and the total will show 1 resource drifted, 3 field(s) changed.

Paths use `[name=<container-name>]` instead of positional `[0]`. Containers are matched by name so the path stays accurate regardless of order in the list.

`spec.replicas` won't show up even if it differs. The manifest has `drift.gitops.io/ignore-fields: "spec.replicas"` so an HPA can manage counts without constantly triggering drift alerts.

---

## 7. Remediation

```bash
python3 -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --remediate \
  --once
```

```
WARNING  root  Remediation mode is ACTIVE -- drift will be corrected automatically
INFO     gitops_drift.reconciler  Reconciling against Git revision <sha>
INFO     gitops_drift.remediator  Remediating Deployment/demo-app in namespace 'default' (3 field(s) drifted)
INFO     gitops_drift.kubernetes_client  Updated Deployment/demo-app in namespace 'default'

Drift Report
  Git revision : <12-char SHA>
============================================================

Deployment/demo-app (ns: default)
  Action : remediated
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

Check it came back:

```bash
kubectl get deployment demo-app -o jsonpath='{.spec.template.spec.containers[0].image}'
# nginx:1.25
```

Re-run dry-run to confirm nothing is still drifted:

```bash
python3 -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --once
```

---

## 8. Continuous loop

Drop `--once` and the controller keeps reconciling every `--interval` seconds:

```bash
python3 -m gitops_drift.main \
  --manifests ./examples/desired \
  --namespace default \
  --dry-run \
  --interval 30
```

`Ctrl+C` to stop.

---

## 9. Demo script

Shortcut for steps 5 and 6: injects drift and runs detection in one shot:

```bash
./scripts/demo-drift.sh
```

To see the controller catch drift live mid-run, use loop mode. It starts the controller in the background, injects drift while it's running, and prints the full output after the next reconciliation cycle:

```bash
LOOP=1 ./scripts/demo-drift.sh
```

The interval defaults to 15 seconds. we can override it with `INTERVAL=<seconds>`:

```bash
LOOP=1 INTERVAL=30 ./scripts/demo-drift.sh
```

---

## 10. Tests

```bash
pytest -v
```

All tests should pass with no failures. With coverage:

```bash
pytest --cov=src/gitops_drift --cov-report=term-missing
```

---

## 11. Tear down

```bash
./scripts/cleanup.sh
```

Deletes the kind cluster and cleans up the Docker network.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'kubernetes'`**
Venv isn't active. `source .venv/bin/activate`, then `pip install -e ".[dev]"` from the repo root.

**`command not found: python`**
macOS doesn't ship a bare `python` binary. Activate the venv or just call `python3` directly.

**`Could not load Kubernetes config`**
Run `kubectl cluster-info`. If the cluster is gone, re-run `setup-kind.sh`. If you have multiple contexts, check `kubectl config current-context`.

**`Cannot connect to the Docker daemon`**
Docker Desktop isn't running. `open -a Docker` and give it a moment to start.

**`kind: command not found` after `brew install kind`**
Homebrew isn't on PATH. Source the right shellenv for your machine and retry:
- Apple Silicon: `eval "$(/opt/homebrew/bin/brew shellenv)"`
- Intel: `eval "$(/usr/local/bin/brew shellenv)"`

**Lots of unexpected fields in the drift report**
Manifest is too noisy. Don't paste directly from `kubectl get -o yaml` without stripping server-side fields first.

**`ApiException: (409) Conflict` during remediation**
Race between the fetch and replace. The error is logged and skipped; the next loop iteration picks up the fresh state.

**k3d: `kubectl` commands fail with `connection refused` after cluster creation**
k3d may take a few seconds to be fully ready. Run `kubectl wait --for=condition=Ready node --all --timeout=60s` and retry.

**k3d: context name is wrong**
k3d prefixes cluster names with `k3d-`. If you created `drift-demo`, the context is `k3d-drift-demo`. Check with `kubectl config get-contexts`.
