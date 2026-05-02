"""Recursive diff engine for comparing desired vs live Kubernetes state."""

from typing import Any, Dict, List


def compute_diff(desired: Any, live: Any, path: str = "") -> List[Dict]:
    """
    Recursively compare two normalized objects and return a list of drift entries.

    Each entry is a dict with:
        path     - dot-notation field path where the diff was found
        desired  - value from the Git manifest
        live     - value from the cluster
    """
    diffs = []

    if isinstance(desired, dict) and isinstance(live, dict):
        all_keys = set(desired.keys()) | set(live.keys())
        for key in sorted(all_keys):
            child_path = f"{path}.{key}" if path else key
            if key not in live:
                diffs.append({
                    "path": child_path,
                    "desired": desired[key],
                    "live": "<missing>",
                })
            elif key not in desired:
                # A field exists on the cluster that isn't in the desired manifest.
                # We intentionally do NOT flag this as drift -- Kubernetes and
                # admission webhooks routinely inject defaults (e.g. terminationMessagePath,
                # imagePullPolicy when omitted). Flagging them would be very noisy and
                # rarely actionable.
                pass
            else:
                diffs.extend(compute_diff(desired[key], live[key], child_path))

    elif isinstance(desired, list) and isinstance(live, list):
        if _is_named_list(desired) and _is_named_list(live):
            # Semantic matching: pair items by their 'name' key.
            # This prevents false positives when container order differs between
            # the desired manifest and live cluster state -- a real occurrence
            # when admission webhooks inject sidecars or operators reorder containers.
            live_by_name = {item["name"]: item for item in live}
            for desired_item in desired:
                name = desired_item["name"]
                child_path = f"{path}[name={name}]"
                if name not in live_by_name:
                    diffs.append({"path": child_path, "desired": desired_item, "live": "<missing>"})
                else:
                    diffs.extend(compute_diff(desired_item, live_by_name[name], child_path))
        else:
            # Positional comparison for lists without consistent name keys
            # (e.g. plain string lists, port lists without names).
            max_len = max(len(desired), len(live))
            for i in range(max_len):
                child_path = f"{path}[{i}]"
                if i >= len(live):
                    diffs.append({"path": child_path, "desired": desired[i], "live": "<missing>"})
                elif i >= len(desired):
                    # Same logic as dict: extra items on the live side are cluster-injected defaults.
                    pass
                else:
                    diffs.extend(compute_diff(desired[i], live[i], child_path))

    else:
        if desired != live:
            diffs.append({"path": path, "desired": desired, "live": live})

    return diffs


def _is_named_list(lst: list) -> bool:
    """Return True if every element is a dict with a 'name' key.

    Kubernetes uses named lists for containers, volumes, env vars, ports, and
    init containers. Matching by name is semantically correct for all of them.
    """
    return bool(lst) and all(isinstance(item, dict) and "name" in item for item in lst)
