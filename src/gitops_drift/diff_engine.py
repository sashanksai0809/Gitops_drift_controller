"""Recursive diff engine for comparing desired vs live Kubernetes state."""

from typing import Any, Dict, List, Optional


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
        # Lists are compared element-by-element by position. This is a pragmatic
        # choice -- semantic list diffing (e.g. matching containers by name) is
        # complex and adds explanation overhead that doesn't pay off for a scoped tool.
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
