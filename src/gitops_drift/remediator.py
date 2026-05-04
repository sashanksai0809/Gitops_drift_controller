"""Handles optional drift remediation by re-applying desired manifests."""

import logging
from typing import Dict, List

from .kubernetes_client import apply_manifest

logger = logging.getLogger(__name__)


def remediate(manifest: Dict, diffs: List[Dict]) -> str:
    """Re-apply the desired manifest to the cluster via full replace.

    Ignored-field values are injected before this is called (see
    _manifest_for_remediation), so excluded fields like HPA-managed replicas
    keep their live values. Server-side apply with a named field manager is
    the right long-term approach; full replace is used here for simplicity.
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
