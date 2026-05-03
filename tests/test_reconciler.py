"""End-to-end tests for a single reconciliation cycle."""

import subprocess

import yaml

from gitops_drift.config import ControllerConfig
from gitops_drift.reconciler import run_once, _get_git_revision


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
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, **_kwargs: None)

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

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir, **_kw: [manifest])
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
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, **_kw: None)

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

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir, **_kw: [manifest])
    monkeypatch.setattr("gitops_drift.reconciler.fetch_live_resource", fake_fetch_live_resource)
    monkeypatch.setattr(
        "gitops_drift.reconciler.remediate",
        lambda manifest_arg, _diffs: remediated.append(manifest_arg) or "remediated",
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _entries, **_kwargs: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, **_kw: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", dry_run=False, remediate=True)
    run_once(cfg)

    assert fetch_calls == [("Namespace", "team-a", "")]
    assert "namespace" not in remediated[0]["metadata"]
    assert "namespace" not in manifest["metadata"]


def test_run_once_threads_json_output_to_reporter(monkeypatch):
    report_calls = []

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir, **_kw: [])
    monkeypatch.setattr(
        "gitops_drift.reconciler.print_report",
        lambda entries, as_json=False, revision=None: report_calls.append((entries, as_json)),
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _entries, **_kw: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", output="json")
    run_once(cfg)

    assert report_calls == [([], True)]


# ---------------------------------------------------------------------------
# Git revision tracking tests
# ---------------------------------------------------------------------------

def test_get_git_revision_returns_sha_for_git_repo(tmp_path):
    """Revision must be a full 40-char hex SHA when the directory is inside a git repo."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "ci@test.local"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=str(tmp_path), capture_output=True)
    (tmp_path / "manifest.yaml").write_text("kind: ConfigMap\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, check=True)

    revision = _get_git_revision(str(tmp_path))

    assert len(revision) == 40
    assert all(c in "0123456789abcdef" for c in revision)


def test_get_git_revision_returns_unknown_outside_repo(tmp_path):
    """Revision should degrade gracefully to 'unknown' in a plain directory."""
    assert _get_git_revision(str(tmp_path)) == "unknown"


def test_get_git_revision_returns_unknown_for_nonexistent_directory():
    """Revision should degrade gracefully when the directory does not exist."""
    assert _get_git_revision("/nonexistent/path/xyz") == "unknown"


# ---------------------------------------------------------------------------
# Remediation safety tests
# ---------------------------------------------------------------------------

def test_remediation_preserves_ignored_fields_from_live(monkeypatch):
    """
    Ignored fields must keep their live values during remediation.

    Scenario: spec.replicas is excluded via annotation (simulating an HPA).
    The HPA has scaled the Deployment to 5 replicas. The desired manifest says 2.
    The container image has drifted (nginx:1.19 vs desired nginx:1.25).

    Expected: remediation fixes the image but sends spec.replicas=5 (live value)
    in the apply body, not spec.replicas=2 from the manifest.
    """
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "web",
            "namespace": "default",
            "annotations": {"drift.gitops.io/ignore-fields": "spec.replicas"},
        },
        "spec": {
            "replicas": 2,
            "template": {"spec": {"containers": [
                {"name": "app", "image": "nginx:1.25"},
            ]}},
        },
    }
    live = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {
            "replicas": 5,  # HPA-managed; must be preserved
            "template": {"spec": {"containers": [
                {"name": "app", "image": "nginx:1.19"},  # drifted
            ]}},
        },
    }
    remediation_bodies = []

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir, **_kw: [manifest])
    monkeypatch.setattr("gitops_drift.reconciler.fetch_live_resource", lambda *_: live)
    monkeypatch.setattr(
        "gitops_drift.reconciler.remediate",
        lambda body, diffs: remediation_bodies.append(body) or "remediated",
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _e, **_kw: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _e, **_kw: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", dry_run=False, remediate=True)
    entries = run_once(cfg)

    assert len(entries) == 1, "Image drift should be detected"
    assert entries[0]["action"] == "remediated"

    body = remediation_bodies[0]
    assert body["spec"]["replicas"] == 5, (
        "spec.replicas must carry the LIVE value (5) in the remediation body "
        "to avoid overwriting the HPA-managed scale with the manifest value (2)"
    )


def test_remediation_does_not_preserve_fields_when_no_ignore(monkeypatch):
    """Without ignore fields, the manifest value is sent as-is (existing behaviour)."""
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 2},
    }
    live = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 5},
    }
    remediation_bodies = []

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir, **_kw: [manifest])
    monkeypatch.setattr("gitops_drift.reconciler.fetch_live_resource", lambda *_: live)
    monkeypatch.setattr(
        "gitops_drift.reconciler.remediate",
        lambda body, diffs: remediation_bodies.append(body) or "remediated",
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _e, **_kw: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _e, **_kw: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", dry_run=False, remediate=True)
    run_once(cfg)

    assert remediation_bodies[0]["spec"]["replicas"] == 2


def test_manifest_for_remediation_is_not_mutated(monkeypatch):
    """The on-disk manifest object must never be mutated by the remediation path."""
    import yaml

    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "web",
            "annotations": {"drift.gitops.io/ignore-fields": "spec.replicas"},
        },
        "spec": {"replicas": 2},
    }
    original_replicas = desired["spec"]["replicas"]

    live = {"kind": "Deployment", "metadata": {"name": "web"}, "spec": {"replicas": 7}}

    monkeypatch.setattr("gitops_drift.reconciler.load_manifests", lambda _dir, **_kw: [desired])
    monkeypatch.setattr("gitops_drift.reconciler.fetch_live_resource", lambda *_: live)
    monkeypatch.setattr("gitops_drift.reconciler.remediate", lambda body, diffs: "remediated")
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _e, **_kw: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _e, **_kw: None)

    cfg = ControllerConfig(manifests_dir="unused", namespace="default", dry_run=False, remediate=True)
    run_once(cfg)

    # The original manifest object must not have been mutated.
    assert desired["spec"]["replicas"] == original_replicas, (
        "_manifest_for_remediation must deep-copy before modifying"
    )


def test_run_once_reports_would_create_when_resource_missing(monkeypatch, tmp_path):
    """A resource defined in Git but absent from the cluster must appear as would-create."""
    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 3},
    }
    (tmp_path / "deployment.yaml").write_text(yaml.safe_dump(desired))

    monkeypatch.setattr("gitops_drift.reconciler.fetch_live_resource", lambda *_: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _e, **_kw: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _e, **_kw: None)

    cfg = ControllerConfig(manifests_dir=str(tmp_path), namespace="default", dry_run=True)
    entries = run_once(cfg)

    assert len(entries) == 1
    assert entries[0]["action"] == "would-create (dry-run)"
    assert entries[0]["fields"][0]["path"] == "<resource>"


def test_run_once_alert_only_action_without_dry_run_or_remediate(monkeypatch, tmp_path):
    """--no-dry-run without --remediate should produce action 'drift-detected' (no suffix)."""
    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 3},
    }
    (tmp_path / "deployment.yaml").write_text(yaml.safe_dump(desired))

    monkeypatch.setattr(
        "gitops_drift.reconciler.fetch_live_resource",
        lambda *_: {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "web", "namespace": "default", "resourceVersion": "1"},
            "spec": {"replicas": 1},
        },
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _e, **_kw: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _e, **_kw: None)

    cfg = ControllerConfig(manifests_dir=str(tmp_path), namespace="default", dry_run=False, remediate=False)
    entries = run_once(cfg)

    assert len(entries) == 1
    assert entries[0]["action"] == "drift-detected"


def test_run_once_logs_git_revision(monkeypatch, tmp_path, caplog):
    """run_once must log the Git revision so every cycle is auditable."""
    import logging

    desired = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 2},
    }
    (tmp_path / "deployment.yaml").write_text(yaml.safe_dump(desired))

    monkeypatch.setattr(
        "gitops_drift.reconciler.fetch_live_resource",
        lambda kind, name, namespace: {**desired, "metadata": {**desired["metadata"], "resourceVersion": "1"}},
    )
    monkeypatch.setattr("gitops_drift.reconciler.print_report", lambda _e, **_kw: None)
    monkeypatch.setattr("gitops_drift.reconciler.print_summary", lambda _e, **_kw: None)
    monkeypatch.setattr("gitops_drift.reconciler._get_git_revision", lambda _d: "deadbeef" * 5)

    cfg = ControllerConfig(manifests_dir=str(tmp_path), namespace="default", dry_run=True)

    with caplog.at_level(logging.INFO, logger="gitops_drift.reconciler"):
        run_once(cfg)

    assert any("deadbeef" in record.message for record in caplog.records)
