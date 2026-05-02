"""Loads Kubernetes manifests from a local directory."""

import logging
import os
from typing import Dict, List, Tuple

import yaml

from .config import SUPPORTED_KINDS

logger = logging.getLogger(__name__)


def load_manifests(directory: str, default_namespace: str = "default") -> List[Dict]:
    """
    Walk a directory and parse every .yaml / .yml file as Kubernetes manifests.
    Multi-document YAML files (separated by ---) are fully supported.
    Resources with unsupported kinds are skipped with a warning.
    Duplicate resources (same kind/namespace/name) are skipped with a warning;
    only the first definition encountered is used.
    """
    manifests = []
    seen_keys: set = set()

    if not os.path.isdir(directory):
        raise ValueError(f"Manifests directory does not exist: {directory}")

    for root, _, files in os.walk(directory):
        for fname in sorted(files):
            if not (fname.endswith(".yaml") or fname.endswith(".yml")):
                continue

            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    docs = list(yaml.safe_load_all(f))
            except yaml.YAMLError as e:
                logger.warning("Skipping %s: YAML parse error: %s", fpath, e)
                continue

            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                kind = doc.get("kind", "")
                if kind not in SUPPORTED_KINDS:
                    logger.warning("Skipping unsupported kind '%s' in file %s", kind, fpath)
                    continue

                key = _dedupe_key(doc, default_namespace)
                if key in seen_keys:
                    logger.warning(
                        "Duplicate resource %s/%s (namespace=%s) in %s -- "
                        "only the first definition is used",
                        key[0], key[2], key[1] or "<cluster-scoped>", fpath,
                    )
                    continue

                seen_keys.add(key)
                manifests.append(doc)
                logger.debug("Loaded %s/%s from %s", kind, _resource_name(doc), fpath)

    logger.info("Loaded %d manifest(s) from %s", len(manifests), directory)
    return manifests


def resource_key(manifest: Dict) -> Tuple[str, str, str]:
    """Return a stable (kind, namespace, name) tuple for a manifest."""
    kind = manifest.get("kind", "")
    name = manifest.get("metadata", {}).get("name", "")
    namespace = manifest.get("metadata", {}).get("namespace", "")
    return (kind, namespace, name)


def _dedupe_key(manifest: Dict, default_namespace: str) -> Tuple[str, str, str]:
    kind = manifest.get("kind", "")
    name = manifest.get("metadata", {}).get("name", "")
    if kind == "Namespace":
        namespace = ""
    else:
        namespace = manifest.get("metadata", {}).get("namespace", "") or default_namespace
    return (kind, namespace, name)


def _resource_name(manifest: Dict) -> str:
    return manifest.get("metadata", {}).get("name", "<unknown>")
