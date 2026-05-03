"""Small helper around the Kubernetes Python client."""

import copy
import logging
from typing import Dict, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

_RAW_KEY_MAPS = {
    "annotations",
    "labels",
    "matchLabels",
    "nodeSelector",
    "data",
    "binaryData",
    "stringData",
}


def load_kube_config(kubeconfig: Optional[str] = None) -> None:
    """
    Load Kubernetes credentials from an explicit kubeconfig path, the default
    local kubeconfig, or in-cluster service account config.
    """
    try:
        config.load_kube_config(config_file=kubeconfig)
        logger.debug("Loaded kubeconfig (path=%s)", kubeconfig or "~/.kube/config")
    except config.ConfigException:
        logger.debug("No kubeconfig found, trying in-cluster config")
        config.load_incluster_config()


def fetch_live_resource(kind: str, name: str, namespace: str) -> Optional[Dict]:
    """
    Fetch a single live resource from the cluster and return it as a plain dict.
    Returns None if the resource does not exist.
    Raises on unexpected API errors so the caller can decide how to handle them.
    """
    try:
        raw = _get_resource(kind, name, namespace)
        if raw is None:
            return None
        # The Kubernetes client exposes Python attribute names; convert them
        # back to manifest-style keys before diffing or replacing resources.
        return _normalize_client_dict(raw.to_dict())
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def apply_manifest(manifest: Dict) -> None:
    """
    Apply (create or replace) a manifest on the cluster.
    Used only in remediation mode.
    """
    kind = manifest.get("kind", "")
    name = manifest["metadata"]["name"]
    namespace = manifest.get("metadata", {}).get("namespace", "")

    try:
        existing = fetch_live_resource(kind, name, namespace)
        if existing is None:
            _create_resource(kind, namespace, manifest)
            logger.info("Created %s/%s in namespace '%s'", kind, name, namespace)
        else:
            body = copy.deepcopy(manifest)
            resource_version = existing.get("metadata", {}).get("resourceVersion")
            if resource_version:
                # Kubernetes replace calls require the current resourceVersion.
                body.setdefault("metadata", {})["resourceVersion"] = resource_version
            _replace_resource(kind, name, namespace, body)
            logger.info("Updated %s/%s in namespace '%s'", kind, name, namespace)
    except ApiException as e:
        logger.error("Failed to apply %s/%s: %s", kind, name, e)
        raise


# ---------------------------------------------------------------------------
# Internal helpers -- one per supported resource type
# ---------------------------------------------------------------------------

def _get_resource(kind: str, name: str, namespace: str):
    apps = client.AppsV1Api()
    core = client.CoreV1Api()

    if kind == "Deployment":
        return apps.read_namespaced_deployment(name=name, namespace=namespace)
    if kind == "Service":
        return core.read_namespaced_service(name=name, namespace=namespace)
    if kind == "ConfigMap":
        return core.read_namespaced_config_map(name=name, namespace=namespace)
    if kind == "Namespace":
        return core.read_namespace(name=name)
    raise ValueError(f"Unsupported kind: {kind}")


def _create_resource(kind: str, namespace: str, body: Dict) -> None:
    apps = client.AppsV1Api()
    core = client.CoreV1Api()

    if kind == "Deployment":
        apps.create_namespaced_deployment(namespace=namespace, body=body)
    elif kind == "Service":
        core.create_namespaced_service(namespace=namespace, body=body)
    elif kind == "ConfigMap":
        core.create_namespaced_config_map(namespace=namespace, body=body)
    elif kind == "Namespace":
        core.create_namespace(body=body)
    else:
        raise ValueError(f"Unsupported kind: {kind}")


def _replace_resource(kind: str, name: str, namespace: str, body: Dict) -> None:
    apps = client.AppsV1Api()
    core = client.CoreV1Api()

    if kind == "Deployment":
        apps.replace_namespaced_deployment(name=name, namespace=namespace, body=body)
    elif kind == "Service":
        core.replace_namespaced_service(name=name, namespace=namespace, body=body)
    elif kind == "ConfigMap":
        core.replace_namespaced_config_map(name=name, namespace=namespace, body=body)
    elif kind == "Namespace":
        core.replace_namespace(name=name, body=body)
    else:
        raise ValueError(f"Unsupported kind: {kind}")


def _normalize_client_dict(value):
    if isinstance(value, list):
        return [_normalize_client_dict(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = {}
    for key, child in value.items():
        normalized_key = _snake_to_lower_camel(key)
        if normalized_key in _RAW_KEY_MAPS:
            normalized[normalized_key] = child
        else:
            normalized[normalized_key] = _normalize_client_dict(child)
    return normalized


def _snake_to_lower_camel(key: str) -> str:
    if "_" not in key:
        return key
    first, *rest = key.split("_")
    return first + "".join(part.capitalize() for part in rest)
