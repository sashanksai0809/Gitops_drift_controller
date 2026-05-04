"""Formats and prints drift reports."""

import json
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

# ANSI colours for terminal output
_RED = "\033[31m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def build_report_entry(
    kind: str,
    name: str,
    namespace: str,
    diffs: List[Dict],
    action: str,
) -> Dict:
    """Return a structured report entry for one resource."""
    return {
        "kind": kind,
        "name": name,
        "namespace": namespace,
        "drift_count": len(diffs),
        "fields": [
            {
                "path": d["path"],
                "desired": d["desired"],
                "live": d["live"],
            }
            for d in diffs
        ],
        "action": action,
    }


def print_report(entries: List[Dict], as_json: bool = False, revision: str = None) -> None:
    """Print the full drift report to stdout."""
    if as_json:
        report = {
            "revision": revision or "unknown",
            "resources": entries,
        }
        print(json.dumps(report, indent=2, default=str))
        return

    rev_label = f"  Git revision : {_BOLD}{(revision or 'unknown')[:12]}{_RESET}"

    if not entries:
        print(f"\n{_GREEN}{_BOLD}No drift detected.{_RESET}")
        print(rev_label + "\n")
        return

    print(f"\n{_BOLD}Drift Report{_RESET}")
    print(rev_label)
    print("=" * 60)

    for entry in entries:
        resource_id = f"{entry['kind']}/{entry['name']} (ns: {entry['namespace']})"

        print(f"\n{_BOLD}{_YELLOW}{resource_id}{_RESET}")
        print(f"  Action : {_action_color(entry['action'])}{entry['action']}{_RESET}")
        print(f"  Fields : {entry['drift_count']} drifted")

        for f in entry["fields"]:
            print(f"    {_BOLD}{f['path']}{_RESET}")
            print(f"      desired : {_GREEN}{f['desired']}{_RESET}")
            print(f"      live    : {_RED}{f['live']}{_RESET}")

    print("\n" + "=" * 60)
    total = sum(e["drift_count"] for e in entries)
    print(f"Total: {len(entries)} resource(s) drifted, {total} field(s) changed\n")


def print_summary(entries: List[Dict], dry_run: bool, remediate: bool = False, revision: str = None) -> None:
    if dry_run:
        mode = "dry-run"
    elif remediate:
        mode = "remediation"
    else:
        mode = "alert"
    logger.info(
        "Reconciliation complete [mode=%s, revision=%s]: %d drifted resource(s)",
        mode,
        (revision or "unknown")[:12],
        len(entries),
    )


def _action_color(action: str) -> str:
    if "remediation-failed" in action.lower():
        return _RED
    if "remediat" in action.lower() or "applied" in action.lower():
        return _GREEN
    if "dry" in action.lower():
        return _YELLOW
    return ""
