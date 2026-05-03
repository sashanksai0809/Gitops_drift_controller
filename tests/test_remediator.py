"""Tests for remediator.remediate() -- success and failure paths."""

from unittest.mock import patch

from gitops_drift.remediator import remediate

MANIFEST = {
    "kind": "Deployment",
    "metadata": {"name": "demo-app", "namespace": "default"},
    "spec": {"replicas": 2},
}

DIFFS = [{"path": "spec.replicas", "desired": 2, "live": 5}]


def test_remediate_returns_remediated_on_success():
    with patch("gitops_drift.remediator.apply_manifest") as mock_apply:
        result = remediate(MANIFEST, DIFFS)
    mock_apply.assert_called_once_with(MANIFEST)
    assert result == "remediated"


def test_remediate_returns_failure_string_on_exception():
    with patch("gitops_drift.remediator.apply_manifest", side_effect=Exception("(409) Conflict")):
        result = remediate(MANIFEST, DIFFS)
    assert result.startswith("remediation-failed:")
    assert "409" in result


def test_remediate_logs_drifted_field_count(caplog):
    with patch("gitops_drift.remediator.apply_manifest"):
        import logging
        with caplog.at_level(logging.INFO, logger="gitops_drift.remediator"):
            remediate(MANIFEST, DIFFS)
    assert "1 field(s) drifted" in caplog.text
