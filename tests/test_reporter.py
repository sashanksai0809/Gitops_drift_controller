"""Tests for drift report output."""

import json

from gitops_drift.reporter import print_report


def test_print_report_json_output(capsys):
    entries = [
        {
            "kind": "Deployment",
            "name": "web",
            "namespace": "default",
            "drift_count": 1,
            "fields": [{"path": "spec.replicas", "desired": 3, "live": 1}],
            "action": "drift-detected (dry-run)",
        }
    ]

    print_report(entries, as_json=True)

    assert json.loads(capsys.readouterr().out) == entries
