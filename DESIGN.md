# Design Document: GitOps Drift Detection Controller

## 1. How do you define drift? What fields do you compare, and what do you explicitly ignore and why?

**Drift** is any difference between what a Kubernetes manifest in Git declares and what the live cluster actually holds, after stripping fields that Kubernetes manages automatically.

### What we compare

We compare every field that appears in the desired manifest. If the desired manifest declares `spec.template.spec.containers[0].image: nginx:1.25` and the live cluster has `nginx:1.19`, that is drift.

We use a one-directional comparison: fields present in the live object but absent from the desired manifest are not flagged. This is intentional. Kubernetes and admission webhooks inject defaults on resource creation -- `imagePullPolicy`, `terminationMessagePath`, `defaultMode` on volume mounts, and many others. Flagging every injected default would produce a report that is impossible to act on. The assumption is that if you care about a field, you should declare it in your manifest.

### Immutable fields and representation drift

Some fields can drift but cannot be remediated with an update or replace. For example, `spec.selector` on a Deployment is immutable after creation. If Git says `spec.selector.matchLabels.app: web` and the live Deployment was created with `app: api`, Kubernetes will reject a replace with HTTP 422. The controller logs `remediation-failed`, leaves the resource unchanged, and the same drift is detected again on the next reconciliation cycle. That alert will repeat until an operator performs a delete-and-recreate, updates Git to match reality, or excludes the field.

The diff also treats parsed YAML values as values, not as source text. Kubernetes normalizes values such as `defaultMode: 0644` to the API-server representation `420`. Manifests should use the API representation, for example decimal `420`; quoting `"0644"` changes the type to string and can introduce drift.

Service `spec.clusterIP` is assigned by Kubernetes and immutable after creation. Desired Service manifests should omit it or match the live assigned value. If Git declares a different `clusterIP`, remediation can fail repeatedly or create recurring drift.

### What we explicitly ignore

**System-managed fields** (stripped before any comparison):

| Field | Reason |
|---|---|
| `metadata.resourceVersion` | Changes on every write; not part of desired state |
| `metadata.uid` | Assigned at creation; cannot be declared |
| `metadata.generation` | Incremented by the API server; not declarable |
| `metadata.creationTimestamp` | Set at creation; not declarable |
| `metadata.managedFields` | Server-side apply bookkeeping |
| `metadata.selfLink` | Deprecated; injected by older API servers |
| `status` | Cluster-managed; never part of desired state |
| `metadata.annotations["kubectl.kubernetes.io/last-applied-configuration"]` | kubectl injects this; it encodes the full previous manifest as JSON and would make every diff noisy |

**Per-resource exclusions** (via annotation):

Fields can be excluded per-resource using `drift.gitops.io/ignore-fields`. This is for fields that are intentionally managed outside of Git -- the canonical example is `spec.replicas` when an HPA is active.

**Global exclusions** (via `--ignore-fields` flag):

The CLI accepts a comma-separated list of field paths to exclude from every resource in the run. Useful for fields that are consistently managed externally across an entire cluster.

---

## 2. How does the exclusion mechanism work? What are its limits?

### How it works

The annotation `drift.gitops.io/ignore-fields` on a desired manifest accepts a comma-separated list of dot-notation paths, for example:

```
drift.gitops.io/ignore-fields: "spec.replicas,metadata.labels.env"
```

At reconciliation time, `reconciler.py` reads this annotation from the desired manifest and passes the paths to `normalizer.normalize()`. The normalizer removes those paths from both the desired and live objects before diffing. Because removal happens to both sides, the field disappears from the comparison entirely. It is neither detected as drift nor remediated.

The normalizer uses a path-split traversal: `spec.replicas` becomes `["spec", "replicas"]`, and `_delete_path` recurses through dictionaries deleting the final key. Deeply nested dictionary paths work, but list indexes do not.

### Limits

**No list-index exclusions.** The path parser splits on `.` and does not understand bracket notation. You cannot exclude `spec.template.spec.containers[0].image`; the traversal looks for a literal key named `containers[0]`. This is a real limitation because container-level exclusions are common in production, especially when image automation, admission controllers, or emergency hotfixes can change `spec.template.spec.containers[0].image`.

The current workaround is to exclude the whole parent object, such as `spec.template.spec.containers` or `spec.template`, depending on how broad the exception needs to be. That reduces signal because unrelated changes under the same parent are also hidden.

**Desired-side annotation only.** The annotation is read from the desired manifest, not the live object. If someone removes the annotation from the live object with `kubectl edit`, it has no effect on the comparison.

**Whole-subtree removal only.** If you ignore `spec.template`, the entire template is excluded. There is no way to say "ignore `spec.template` except for `spec.template.spec.containers[0].image`". This is a reasonable tradeoff for the scope of this tool.

**No wildcard or pattern matching.** Paths must be exact dot-notation strings. You cannot write `spec.template.*` to exclude all template fields.

---

## 3. What is the difference between drift that should alert and drift that should auto-remediate? How does the system decide?

The current system does not make this distinction automatically -- it is left to the operator. The mode is set at run time, not inferred from the nature of the drift.

**The general principle:**

Alert-only drift is drift where human judgment is required before acting. Examples:
- An image tag changed -- this might be an intentional hotfix or a security incident; both warrant review before the old image is restored
- A label was added -- removing it might break traffic routing that depends on it
- A resource is missing from the cluster entirely -- it may have been intentionally deleted

Auto-remediation is appropriate for drift where convergence to the desired state is always safe. Examples:
- A ConfigMap value was changed by a manual `kubectl edit`, and the owning application can tolerate the value being restored
- Resource requests/limits were bumped in a "quick fix" and need to be rolled back

ConfigMap remediation is not automatically safe. Updating a ConfigMap can change behavior for pods that read it dynamically, and many workloads require a rollout before mounted ConfigMap changes are picked up. A ConfigMap that controls feature flags, routing, or credentials should be treated as application-facing change, not harmless metadata.

Deployment remediation can affect live traffic. Replacing a Deployment that changes `spec.template` triggers a rollout, which can restart pods, expose readiness-probe mistakes, or reduce capacity if disruption budgets and surge settings are wrong. Reverting an image can be the right action, but it is still a production rollout.

**How the system decides in practice:**

If `--remediate` is not passed, every drift entry gets action `drift-detected (dry-run)`. The operator reviews the report and decides whether to re-run with `--remediate`.

If `--remediate` is passed, every drifted resource is re-applied. This is a blunt instrument -- it does not distinguish between "safe to auto-fix" and "needs review". For a production controller, you would extend this by adding a per-resource annotation like `drift.gitops.io/remediation-policy: auto|manual` and checking it in `reconciler.py` before calling `remediator.remediate()`.

**Exclusions are never remediated.** A field listed in `drift.gitops.io/ignore-fields` is stripped before diffing, so it never appears in the diff list and is never touched by the remediator. This is the safety mechanism for fields like `spec.replicas` that are managed by an HPA -- the HPA's value is preserved even in remediation mode.

---

## 4. Kubernetes mutates resources after apply by adding resourceVersion, managedFields, etc. How do you handle this in diff logic?

The `normalizer.normalize()` function strips API-server-injected fields from both the desired and live object before passing them to `compute_diff()`. This happens in both directions:

- **Desired object**: The local YAML manifest does not contain these fields, but we still run it through `normalize()` for safety (e.g. if someone accidentally copies a `resourceVersion` into their manifest).
- **Live object**: The API server always returns these fields. Stripping them ensures we only compare what was intentionally declared.

The default system-managed fields are documented in `config.py` as `SYSTEM_MANAGED_FIELDS`. The implementation in `normalizer.py` also strips snake_case equivalents returned by the Kubernetes Python client's `to_dict()`, such as `metadata.resource_version`, `metadata.managed_fields`, and `metadata.creation_timestamp`. The `kubectl.kubernetes.io/last-applied-configuration` annotation is also stripped because it is client bookkeeping, not desired state.

After normalization, the diff is purely value-based: two normalized objects are in sync if and only if every field declared in the desired object has the same value in the live object.

Two metadata edge cases are intentionally left visible unless the operator excludes them. `metadata.ownerReferences` may be added by controllers that adopt resources, and removing it can break garbage collection ownership. Live-only `metadata.finalizers` are ignored by the one-directional diff. Drift occurs only when Git declares a finalizer that is missing from the live object; remediation may re-add it, which can block deletion permanently if the owning controller is no longer present.

---

## 5. What would a production deployment of this controller look like? How does it run, and how does it fail safely?

### Deployment

The controller runs as a Deployment in a dedicated namespace, e.g. `drift-system`. Without leader election it must run as a single replica. Two active replicas can both detect the same drift and race on remediation, producing noisy logs, duplicate alerts, or conflicting replace calls.

It mounts desired-state manifests from a ConfigMap or a Git-synced volume, for example a sidecar running `git-sync`. A stale `git-sync` checkout is a production failure mode: the controller may compare the cluster against an old commit and report false drift, or remediate away a valid newer change. The running commit SHA should be logged and exported as a metric.

It uses a ServiceAccount with a ClusterRole that grants `get` and `list` on the supported resource types, plus `update` if remediation is enabled.

```
drift-system/
  Deployment: drift-controller
    - init container or sidecar: git-sync (clones repo, keeps manifests fresh)
    - main container: gitops-drift --manifests /manifests --interval 60
  ServiceAccount: drift-controller
  ClusterRole: drift-controller (get/list Deployments, Services, ConfigMaps, Namespaces)
  ClusterRoleBinding: drift-controller
  ConfigMap: controller-config (kubeconfig is not needed; in-cluster auth is used)
```

### Failure modes and safety

**API server unavailable**: The reconciliation loop catches exceptions around each `fetch_live_resource` call and continues to the next resource. If the API server is completely unreachable, the loop will log errors for each resource and then sleep until the next interval. It will not crash.

**Manifest parse error**: If a YAML file is malformed, the loader logs a warning and skips it. The rest of the manifests are still processed.

**Remediation failure**: `remediator.remediate()` catches the `ApiException` from the Kubernetes client, logs it, and returns a `remediation-failed` action string. This appears in the drift report but does not abort the loop.

**Invalid configuration**: The only hard failure is an invalid kubeconfig or inability to authenticate. This is caught at startup (`main.py`) and exits with a non-zero code, which causes Kubernetes to restart the container.

**Dry-run by default**: The controller defaults to dry-run mode, so a misconfigured deployment cannot accidentally modify the cluster. Remediation requires an explicit flag.

**No state persistence**: The controller is stateless. It does not write to etcd or any external store. If it is killed mid-cycle, the next run starts fresh. The tradeoff is that it has no drift history or suppression state, so persistent drift can create alert fatigue until the manifest or cluster is fixed.

### Observability

Current logs are plaintext with timestamp, level, logger name, and message. A production controller should emit JSON logs so indexing systems can query `kind`, `namespace`, action, and drift field paths directly. A future iteration would expose a Prometheus `/metrics` endpoint reporting:
- `drift_resources_total{kind,namespace,status="in_sync"|"drifted"}` (gauge, per reconciliation cycle)
- `drift_fields_total{kind,namespace}` (gauge, per reconciliation cycle)
- `reconciliation_duration_seconds` (histogram)
- `remediation_total{kind,namespace,result="success"|"failed"}` (counter)
- `manifest_revision_info{revision}` (gauge set to 1 for the Git revision currently being compared)

### Scaling considerations

The current implementation fetches resources one at a time. For clusters with hundreds of tracked resources, a production version would use the Kubernetes informer framework (shared cache backed by a watch stream) to avoid repeated API calls. Migrating to an informer-based model is a significant rewrite: it needs a workqueue, retry and backoff handling, watch state management, cache synchronization, and careful handling of missed or stale events.
