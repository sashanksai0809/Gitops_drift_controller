"""Strip system-managed fields from Kubernetes objects before diffing."""

import copy
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Sentinel returned by get_nested when a key is absent (distinct from None,
# which is a valid value a field can hold in Kubernetes objects).
_MISSING = object()


def normalize(obj: Dict, extra_ignore: List[str] = None) -> Dict:
    """
    Return a deep copy of `obj` with system-managed fields removed.

    `extra_ignore` lets callers pass additional dot-notation paths to strip,
    e.g. fields listed in the drift.gitops.io/ignore-fields annotation.
    """
    result = copy.deepcopy(obj)

    # Always remove these -- they are written by the API server and are never
    # part of the desired state expressed in a manifest.
    _system_paths = [
        ["metadata", "resourceVersion"],
        ["metadata", "resource_version"],
        ["metadata", "uid"],
        ["metadata", "generation"],
        ["metadata", "creationTimestamp"],
        ["metadata", "creation_timestamp"],
        ["metadata", "managedFields"],
        ["metadata", "managed_fields"],
        ["metadata", "selfLink"],
        ["metadata", "self_link"],
        ["status"],
    ]

    for path in _system_paths:
        _delete_path(result, path)

    # Strip the last-applied annotation kubectl injects -- it encodes the full
    # previous manifest as a JSON string and would clutter the diff output.
    annotations = result.get("metadata", {}).get("annotations") or {}
    annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)

    for field_path in (extra_ignore or []):
        if "[" in field_path:
            logger.warning(
                "Ignore path '%s' contains '['; bracket notation is not supported. "
                "Exclude the parent path instead, for example spec.template.spec.containers.",
                field_path,
            )
        parts = [p.strip() for p in field_path.split(".") if p.strip()]
        _delete_path(result, parts)

    return result


def get_nested(obj: Any, path: List[str]) -> Any:
    """Return the value at a dot-split path, or _MISSING if any key is absent.

    Returns the _MISSING sentinel (not None) so callers can distinguish between
    a key that is absent and a key that is explicitly set to null/None.
    Import _MISSING from this module to check: ``if value is _MISSING``.
    """
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return _MISSING
        obj = obj[key]
    return obj


def set_nested(obj: Dict, path: List[str], value: Any) -> None:
    """Write value into obj at the nested path, creating intermediate dicts as needed."""
    if not path:
        return
    for key in path[:-1]:
        obj = obj.setdefault(key, {})
    obj[path[-1]] = value


def _delete_path(obj: Any, path: List[str]) -> None:
    """Delete a nested key described by a dot-split path list, in place."""
    if not path or not isinstance(obj, dict):
        return
    if len(path) == 1:
        obj.pop(path[0], None)
        return
    child = obj.get(path[0])
    if isinstance(child, dict):
        _delete_path(child, path[1:])
