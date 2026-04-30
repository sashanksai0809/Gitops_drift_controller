"""End-to-end tests for a single reconciliation cycle."""

import yaml

from gitops_drift.config import ControllerConfig
from gitops_drift.reconciler import run_once


def test_run_once_detects_drift_with_mocked_live_resource(tmp_path, monkeypatch):
    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 3},
    }
    (tmp_path / "deployment.yaml").write_text(yaml.safe_dump(desired))

    calls = []

    def fake_fetch_live_resource(kind, name, namespace):
        calls.append((kind, name, namespace))
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "web", "namespace": "default", "resourceVersion": "123"},
            "spec": {"replicas": 1},
        }

    monkeypatch.setattr("gitops_drift.reconciler.fetch_live_resource", fake_fetch_live_resource)
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _entries, **_kwargs: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, dry_run: None)

    cfg = ControllerConfig(manifests_dir=str(tmp_path), namespace="default", dry_run=True)
    entries = run_once(cfg)

    assert calls == [("Deployment", "web", "default")]
    assert len(entries) == 1
    assert entries[0]["action"] == "drift-detected (dry-run)"
    assert entries[0]["fields"][0]["path"] == "spec.replicas"


def test_remediation_uses_default_namespace_without_mutating_manifest(monkeypatch):
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web"},
        "spec": {"replicas": 3},
    }
    remediated = []

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir: [manifest])
    monkeypatch.setattr(
        "gitops_drift.reconciler.fetch_live_resource",
        lambda kind, name, namespace: {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "web", "namespace": namespace},
            "spec": {"replicas": 1},
        },
    )
    monkeypatch.setattr(
        "gitops_drift.reconciler.remediate",
        lambda manifest_arg, _diffs: remediated.append(manifest_arg) or "remediated",
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _entries, **_kwargs: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, dry_run: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", dry_run=False, remediate=True)
    entries = run_once(cfg)

    assert entries[0]["action"] == "remediated"
    assert remediated[0]["metadata"]["namespace"] == "default"
    assert "namespace" not in manifest["metadata"]


def test_namespace_remediation_remains_cluster_scoped(monkeypatch):
    manifest = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "team-a"},
        "spec": {"finalizers": ["kubernetes"]},
    }
    fetch_calls = []
    remediated = []

    def fake_fetch_live_resource(kind, name, namespace):
        fetch_calls.append((kind, name, namespace))
        return {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "team-a"},
            "spec": {"finalizers": []},
        }

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir: [manifest])
    monkeypatch.setattr("gitops_drift.reconciler.fetch_live_resource", fake_fetch_live_resource)
    monkeypatch.setattr(
        "gitops_drift.reconciler.remediate",
        lambda manifest_arg, _diffs: remediated.append(manifest_arg) or "remediated",
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _entries, **_kwargs: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, dry_run: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", dry_run=False, remediate=True)
    run_once(cfg)

    assert fetch_calls == [("Namespace", "team-a", "")]
    assert "namespace" not in remediated[0]["metadata"]
    assert "namespace" not in manifest["metadata"]


def test_run_once_threads_json_output_to_reporter(monkeypatch):
    report_calls = []

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir: [])
    monkeypatch.setattr(
        "gitops_drift.reconciler.print_report",
        lambda entries, as_json=False: report_calls.append((entries, as_json)),
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, dry_run: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", output="json")
    run_once(cfg)

    assert report_calls == [([], True)]
