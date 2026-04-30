"""Handles optional drift remediation by re-applying desired manifests."""

import logging
from typing import Dict, List

from .kubernetes_client import apply_manifest

logger = logging.getLogger(__name__)


def remediate(manifest: Dict, diffs: List[Dict]) -> str:
    """
    Re-apply the desired manifest to the cluster.

    This is intentionally a full replace rather than a strategic merge patch.
    A replace is simpler to reason about and guarantees the cluster state
    converges to exactly what is in Git. The tradeoff is that it will overwrite
    any fields that are legitimately managed outside of Git (e.g. by an operator),
    which is why remediation is opt-in and why the exclusion mechanism exists.
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
