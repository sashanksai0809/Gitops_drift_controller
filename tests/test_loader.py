"""Tests for manifest loading."""

import logging

import yaml

from gitops_drift.loader import load_manifests


def test_unsupported_kind_logs_warning(tmp_path, caplog):
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "batch-job"},
    }
    (tmp_path / "job.yaml").write_text(yaml.safe_dump(manifest))

    with caplog.at_level(logging.WARNING):
        loaded = load_manifests(str(tmp_path))

    assert loaded == []
    assert "Skipping unsupported kind 'Job' in file" in caplog.text


def test_supported_kind_loads(tmp_path):
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "settings"},
        "data": {"mode": "test"},
    }
    (tmp_path / "configmap.yaml").write_text(yaml.safe_dump(manifest))

    loaded = load_manifests(str(tmp_path))

    assert loaded == [manifest]


def test_multi_doc_yaml_loads_supported_documents(tmp_path, caplog):
    config_map = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "settings"},
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "web"},
        "spec": {"ports": [{"port": 80}]},
    }
    unsupported = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "batch-job"},
    }
    (tmp_path / "bundle.yaml").write_text(
        "\n---\n".join(yaml.safe_dump(doc) for doc in [config_map, unsupported, service])
    )

    with caplog.at_level(logging.WARNING):
        loaded = load_manifests(str(tmp_path))

    assert loaded == [config_map, service]
    assert "Skipping unsupported kind 'Job' in file" in caplog.text


def test_malformed_yaml_logs_warning_and_skips_file(tmp_path, caplog):
    (tmp_path / "bad.yaml").write_text("apiVersion: v1\nkind: ConfigMap\nmetadata: [")

    with caplog.at_level(logging.WARNING):
        loaded = load_manifests(str(tmp_path))

    assert loaded == []
    assert "YAML parse error" in caplog.text


def test_duplicate_resource_is_skipped_with_warning(tmp_path, caplog):
    """The second definition of the same kind/namespace/name must be dropped."""
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "config", "namespace": "default"},
        "data": {"key": "first"},
    }
    duplicate = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "config", "namespace": "default"},
        "data": {"key": "second"},
    }
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(manifest))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(duplicate))

    with caplog.at_level(logging.WARNING):
        loaded = load_manifests(str(tmp_path))

    assert len(loaded) == 1
    assert loaded[0]["data"]["key"] == "first"
    assert "Duplicate" in caplog.text


def test_same_name_different_namespace_not_duplicate(tmp_path):
    """Resources with the same name but different namespaces are distinct."""
    m1 = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": "config", "namespace": "team-a"}, "data": {}}
    m2 = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": "config", "namespace": "team-b"}, "data": {}}
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(m1))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(m2))

    loaded = load_manifests(str(tmp_path))
    assert len(loaded) == 2


def test_default_namespace_and_explicit_default_are_duplicates(tmp_path, caplog):
    implicit = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web"},
        "spec": {},
    }
    explicit = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {},
    }
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(implicit))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(explicit))

    with caplog.at_level(logging.WARNING):
        loaded = load_manifests(str(tmp_path), default_namespace="default")

    assert loaded == [implicit]
    assert "Duplicate resource Deployment/web (namespace=default)" in caplog.text
