"""Tests for the exclusion mechanism -- annotation-based field ignores."""

import pytest
from gitops_drift.normalizer import normalize
from gitops_drift.diff_engine import compute_diff
from gitops_drift.reconciler import _get_ignore_fields
from gitops_drift.reporter import build_report_entry


MANIFEST_WITH_IGNORE = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "web",
        "namespace": "default",
        "annotations": {
            "drift.gitops.io/ignore-fields": "spec.replicas, metadata.labels.version",
        },
    },
    "spec": {
        "replicas": 3,
        "selector": {"matchLabels": {"app": "web"}},
    },
}


def test_annotation_parses_correctly():
    fields = _get_ignore_fields(MANIFEST_WITH_IGNORE, [])
    assert "spec.replicas" in fields
    assert "metadata.labels.version" in fields


def test_annotation_merged_with_global():
    fields = _get_ignore_fields(MANIFEST_WITH_IGNORE, ["spec.template"])
    assert "spec.replicas" in fields
    assert "spec.template" in fields


def test_replica_drift_ignored_when_excluded():
    desired = {"spec": {"replicas": 3, "minReadySeconds": 0}}
    live = {"spec": {"replicas": 5, "minReadySeconds": 0}}

    ignore = ["spec.replicas"]
    desired_norm = normalize(desired, extra_ignore=ignore)
    live_norm = normalize(live, extra_ignore=ignore)

    diffs = compute_diff(desired_norm, live_norm)
    assert diffs == [], "spec.replicas should not appear as drift when excluded"


def test_non_ignored_field_still_detected():
    desired = {"spec": {"replicas": 3, "minReadySeconds": 0}}
    live = {"spec": {"replicas": 5, "minReadySeconds": 10}}

    ignore = ["spec.replicas"]
    desired_norm = normalize(desired, extra_ignore=ignore)
    live_norm = normalize(live, extra_ignore=ignore)

    diffs = compute_diff(desired_norm, live_norm)
    paths = [d["path"] for d in diffs]
    assert "spec.minReadySeconds" in paths
    assert not any("replicas" in p for p in paths)


def test_report_entry_structure():
    diffs = [{"path": "spec.replicas", "desired": 3, "live": 5}]
    entry = build_report_entry("Deployment", "web", "default", diffs, "drift-detected (dry-run)")
    assert entry["kind"] == "Deployment"
    assert entry["name"] == "web"
    assert entry["namespace"] == "default"
    assert entry["drift_count"] == 1
    assert entry["fields"][0]["path"] == "spec.replicas"
    assert entry["action"] == "drift-detected (dry-run)"


def test_no_ignore_annotation_returns_empty():
    manifest = {"metadata": {"name": "x", "annotations": {}}}
    fields = _get_ignore_fields(manifest, [])
    assert fields == []


def test_no_annotations_key_returns_empty():
    manifest = {"metadata": {"name": "x"}}
    fields = _get_ignore_fields(manifest, [])
    assert fields == []
