"""Core reconciliation loop: load manifests, compare against live state, report."""

import copy
import logging
from typing import List, Dict

from .config import ControllerConfig, IGNORE_ANNOTATION
from .loader import load_manifests, resource_key
from .kubernetes_client import fetch_live_resource
from .normalizer import normalize
from .diff_engine import compute_diff
from .reporter import build_report_entry, print_report, print_summary
from .remediator import remediate

logger = logging.getLogger(__name__)


def run_once(cfg: ControllerConfig) -> List[Dict]:
    """
    Run a single reconciliation cycle.
    Returns the list of drift report entries (one per drifted resource).
    """
    manifests = load_manifests(cfg.manifests_dir)
    report_entries = []

    for manifest in manifests:
        kind, namespace, name = resource_key(manifest)

        # Use the namespace from the manifest; fall back to the configured default.
        effective_ns = _effective_namespace(kind, namespace, cfg.namespace)

        logger.debug("Checking %s/%s (namespace=%s)", kind, name, effective_ns)

        try:
            live_raw = fetch_live_resource(kind, name, effective_ns)
        except Exception as e:
            logger.error("Could not fetch %s/%s: %s -- skipping", kind, name, e)
            continue

        if live_raw is None:
            logger.warning("%s/%s not found in cluster (namespace=%s)", kind, name, effective_ns)
            action = "would-create"
            if cfg.remediate:
                action = remediate(_manifest_for_remediation(manifest, effective_ns), [])
            report_entries.append(
                build_report_entry(kind, name, effective_ns, [{"path": "<resource>", "desired": "exists", "live": "<missing>"}], action)
            )
            continue

        # Collect per-resource ignore fields from the annotation, then merge
        # with any globally configured extra fields.
        ignore_fields = _get_ignore_fields(manifest, cfg.extra_ignore_fields)

        desired_norm = normalize(manifest, extra_ignore=ignore_fields)
        live_norm = normalize(live_raw, extra_ignore=ignore_fields)

        diffs = compute_diff(desired_norm, live_norm)

        if not diffs:
            logger.debug("%s/%s is in sync", kind, name)
            continue

        if cfg.dry_run and not cfg.remediate:
            action = "drift-detected (dry-run)"
        elif cfg.remediate:
            action = remediate(_manifest_for_remediation(manifest, effective_ns), diffs)
        else:
            action = "drift-detected"

        entry = build_report_entry(kind, name, effective_ns, diffs, action)
        report_entries.append(entry)

        logger.info(
            "Drift found: %s/%s -- %d field(s) changed",
            kind, name, len(diffs),
        )

    print_report(report_entries, as_json=(cfg.output == "json"))
    print_summary(report_entries, dry_run=cfg.dry_run)
    return report_entries


def _get_ignore_fields(manifest: Dict, global_ignores: List[str]) -> List[str]:
    """
    Parse the drift.gitops.io/ignore-fields annotation and merge with global ignores.
    Annotation value is expected to be a comma-separated list of dot-notation paths.
    """
    annotation_val = (
        manifest.get("metadata", {})
        .get("annotations", {})
        .get(IGNORE_ANNOTATION, "")
    )
    annotation_fields = [f.strip() for f in annotation_val.split(",") if f.strip()]
    return annotation_fields + (global_ignores or [])


def _effective_namespace(kind: str, manifest_namespace: str, default_namespace: str) -> str:
    """Return empty namespace for cluster-scoped Namespace resources; otherwise use manifest namespace or configured default."""
    # Namespace is the only cluster-scoped kind in the current supported set.
    if kind == "Namespace":
        return ""
    return manifest_namespace or default_namespace


def _manifest_for_remediation(manifest: Dict, effective_ns: str) -> Dict:
    """Deep-copy manifest and inject effective namespace only when namespaced."""
    manifest_copy = copy.deepcopy(manifest)
    # Do not mutate the original manifest loaded from disk.
    # Namespace is cluster-scoped; do not inject metadata.namespace.
    if manifest_copy.get("kind") != "Namespace":
        manifest_copy.setdefault("metadata", {})["namespace"] = effective_ns
    return manifest_copy
