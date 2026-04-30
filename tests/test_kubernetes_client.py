"""Tests for Kubernetes client helpers."""

from gitops_drift import kubernetes_client


class FakeKubernetesObject:
    def to_dict(self):
        return {
            "api_version": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "web",
                "resource_version": "321",
                "managed_fields": [{"manager": "kubectl"}],
                "labels": {"team_name": "platform"},
            },
            "spec": {"selector": {"match_labels": {"app_name": "web"}}},
        }


def test_fetch_live_resource_normalizes_client_to_dict_keys(monkeypatch):
    monkeypatch.setattr(
        kubernetes_client,
        "_get_resource",
        lambda kind, name, namespace: FakeKubernetesObject(),
    )

    result = kubernetes_client.fetch_live_resource("Deployment", "web", "default")

    assert result["apiVersion"] == "apps/v1"
    assert result["metadata"]["resourceVersion"] == "321"
    assert result["metadata"]["managedFields"] == [{"manager": "kubectl"}]
    assert result["metadata"]["labels"] == {"team_name": "platform"}
    assert result["spec"]["selector"]["matchLabels"] == {"app_name": "web"}


def test_apply_manifest_injects_resource_version_before_replace(monkeypatch):
    replaced = {}

    monkeypatch.setattr(
        kubernetes_client,
        "fetch_live_resource",
        lambda kind, name, namespace: {"metadata": {"resourceVersion": "321"}},
    )
    monkeypatch.setattr(
        kubernetes_client,
        "_replace_resource",
        lambda kind, name, namespace, body: replaced.update(body=body),
    )

    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "web", "namespace": "default"},
        "spec": {"replicas": 3},
    }

    kubernetes_client.apply_manifest(manifest)

    assert replaced["body"]["metadata"]["resourceVersion"] == "321"
    assert "resourceVersion" not in manifest["metadata"]
