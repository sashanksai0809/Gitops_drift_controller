"""Configuration and constants for the drift controller."""

from dataclasses import dataclass, field
from typing import List, Optional

# Fields Kubernetes adds automatically after apply. Keep this list as the
# documented contract; normalizer.py implements these paths plus client
# snake_case equivalents.
SYSTEM_MANAGED_FIELDS = [
    "metadata.resourceVersion",
    "metadata.uid",
    "metadata.generation",
    "metadata.creationTimestamp",
    "metadata.managedFields",
    "metadata.selfLink",
    "status",
]

# Resource types this tool explicitly supports. Keeping the scope narrow is
# intentional -- broader coverage is straightforward to add, but each type
# can have edge cases worth handling individually.
SUPPORTED_KINDS = {"Deployment", "Service", "ConfigMap", "Namespace"}

# Annotation key used to mark fields as intentionally allowed to drift.
# Example: drift.gitops.io/ignore-fields: "spec.replicas,metadata.labels.env"
IGNORE_ANNOTATION = "drift.gitops.io/ignore-fields"


@dataclass
class ControllerConfig:
    manifests_dir: str
    namespace: str
    dry_run: bool = True
    remediate: bool = False
    interval: int = 60
    once: bool = False
    kubeconfig: Optional[str] = None
    output: str = "text"
    extra_ignore_fields: List[str] = field(default_factory=list)
    fail_on_drift: bool = False
