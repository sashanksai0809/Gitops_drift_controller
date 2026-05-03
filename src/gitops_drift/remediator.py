"""Handles optional drift remediation by re-applying desired manifests."""

import logging
from typing import Dict, List

from .kubernetes_client import apply_manifest

logger = logging.getLogger(__name__)


def remediate(manifest: Dict, diffs: List[Dict]) -> str:
    """
    Re-apply the desired manifest to the cluster.

    This controller uses a full replace because it is simple to reason about and
    attempts to converge the resource to Git. Ignored-field preservation happens
    before this function is called, so fields such as HPA-managed replicas keep
    their live values in the replace body. Server-side apply with a dedicated
    field manager is the recommended production path.
    """
    kind = manifest.get("kind", "")
    name = manifest.get("metadata", {}).get("name", "")
    namespace = manifest.get("metadata", {}).get("namespace", "")

    logger.info(
        "Remediating %s/%s in namespace '%s' (%d field(s) drifted)",
        kind, name, namespace, len(diffs),
    )

    try:
        apply_manifest(manifest)
        return "remediated"
    except Exception as e:
        logger.error("Remediation failed for %s/%s: %s", kind, name, e)
        return f"remediation-failed: {e}"
