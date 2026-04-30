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
