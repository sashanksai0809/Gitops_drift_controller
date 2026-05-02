"""Tests for drift report output."""

import json

from gitops_drift.reporter import print_report


def test_print_report_json_includes_revision_envelope(capsys):
    """JSON output must be a structured envelope so consumers can correlate results with a Git commit."""
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

    print_report(entries, as_json=True, revision="abc123def456" * 3 + "abc1")

    output = json.loads(capsys.readouterr().out)
    assert output["revision"].startswith("abc123def456")
    assert output["resources"] == entries


def test_print_report_json_revision_defaults_to_unknown(capsys):
    print_report([], as_json=True)
    output = json.loads(capsys.readouterr().out)
    assert output["revision"] == "unknown"
    assert output["resources"] == []


def test_print_report_text_no_drift_shows_revision(capsys):
    print_report([], revision="deadbeef1234")
    out = capsys.readouterr().out
    assert "No drift detected" in out
    assert "deadbeef1234" in out


def test_print_report_text_drift_shows_revision(capsys):
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
    print_report(entries, revision="cafebabe5678")
    out = capsys.readouterr().out
    assert "cafebabe5678" in out
    assert "spec.replicas" in out
