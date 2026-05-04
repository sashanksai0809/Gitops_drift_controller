"""Core reconciliation loop: load manifests, compare against live state, report."""

import copy
import logging
import os
import subprocess
from typing import List, Dict

from .config import ControllerConfig, IGNORE_ANNOTATION
from .loader import load_manifests, resource_key
from .kubernetes_client import fetch_live_resource
from .normalizer import normalize, get_nested, set_nested, _MISSING
from .diff_engine import compute_diff
from .reporter import build_report_entry, print_report, print_summary
from .remediator import remediate

logger = logging.getLogger(__name__)


def run_once(cfg: ControllerConfig) -> List[Dict]:
    """Run a single reconciliation cycle and return the drift report entries."""
    revision = _get_git_revision(cfg.manifests_dir)
    logger.info("Reconciling against Git revision %s", revision)

    manifests = load_manifests(cfg.manifests_dir, default_namespace=cfg.namespace)
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
            if cfg.remediate:
                action = remediate(_manifest_for_remediation(manifest, effective_ns), [])
            elif cfg.dry_run:
                action = "would-create (dry-run)"
            else:
                action = "would-create"
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
            # Pass live_raw and ignore_fields so the remediation body preserves
            # values of ignored fields rather than overwriting them.
            action = remediate(
                _manifest_for_remediation(manifest, effective_ns, live_raw, ignore_fields),
                diffs,
            )
        else:
            action = "drift-detected"

        entry = build_report_entry(kind, name, effective_ns, diffs, action)
        report_entries.append(entry)

        logger.info(
            "Drift found: %s/%s -- %d field(s) changed",
            kind, name, len(diffs),
        )

    print_report(report_entries, as_json=(cfg.output == "json"), revision=revision)
    print_summary(report_entries, dry_run=cfg.dry_run, remediate=cfg.remediate, revision=revision)
    return report_entries


def _get_git_revision(directory: str) -> str:
    """Return the HEAD commit SHA for the manifests directory.

    Used to log exactly which Git commit the controller is comparing against.
    Returns 'unknown' if the directory is not inside a git repo or git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.abspath(directory),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


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


def _manifest_for_remediation(
    manifest: Dict,
    effective_ns: str,
    live_raw: Dict = None,
    ignore_fields: List[str] = None,
) -> Dict:
    """Build the remediation body for a full replace.

    Deep-copies the manifest so the in-memory object is never modified.
    Injects the effective namespace for namespaced resources.

    For each ignored field, reads the live value from the cluster and writes
    it into the copy. Without this, a full replace would send the manifest's
    declared value (e.g. spec.replicas: 2) and silently undo whatever an HPA
    or operator had set on the cluster.
    """
    manifest_copy = copy.deepcopy(manifest)

    if manifest_copy.get("kind") != "Namespace":
        manifest_copy.setdefault("metadata", {})["namespace"] = effective_ns

    if ignore_fields and live_raw:
        for field_path in ignore_fields:
            parts = [p.strip() for p in field_path.split(".") if p.strip()]
            if not parts:
                continue
            live_value = get_nested(live_raw, parts)
            if live_value is not _MISSING:
                set_nested(manifest_copy, parts, live_value)
                logger.debug(
                    "Preserved live value for ignored field '%s' in remediation body",
                    field_path,
                )

    return manifest_copy
