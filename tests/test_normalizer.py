"""Tests for the normalizer -- ensures system-managed fields are stripped cleanly."""

import pytest
from gitops_drift.normalizer import normalize


FULL_DEPLOYMENT = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "my-app",
        "namespace": "default",
        "resourceVersion": "12345",
        "uid": "abc-def-ghi",
        "generation": 3,
        "creationTimestamp": "2024-01-01T00:00:00Z",
        "selfLink": "/apis/apps/v1/namespaces/default/deployments/my-app",
        "managedFields": [{"manager": "kubectl", "operation": "Apply"}],
        "annotations": {
            "kubectl.kubernetes.io/last-applied-configuration": '{"apiVersion":"apps/v1"}',
            "my-team/owner": "platform",
        },
    },
    "spec": {
        "replicas": 3,
        "selector": {"matchLabels": {"app": "my-app"}},
    },
    "status": {
        "availableReplicas": 3,
        "readyReplicas": 3,
    },
}


def test_removes_resource_version():
    result = normalize(FULL_DEPLOYMENT)
    assert "resourceVersion" not in result["metadata"]


def test_removes_uid():
    result = normalize(FULL_DEPLOYMENT)
    assert "uid" not in result["metadata"]


def test_removes_generation():
    result = normalize(FULL_DEPLOYMENT)
    assert "generation" not in result["metadata"]


def test_removes_creation_timestamp():
    result = normalize(FULL_DEPLOYMENT)
    assert "creationTimestamp" not in result["metadata"]


def test_removes_managed_fields():
    result = normalize(FULL_DEPLOYMENT)
    assert "managedFields" not in result["metadata"]


def test_removes_snake_case_system_fields():
    obj = {
        "metadata": {
            "resource_version": "12345",
            "creation_timestamp": "2024-01-01T00:00:00Z",
            "managed_fields": [{"manager": "kubectl"}],
            "self_link": "/api/v1/namespaces/default/configmaps/x",
        }
    }

    result = normalize(obj)

    assert result["metadata"] == {}


def test_removes_self_link():
    result = normalize(FULL_DEPLOYMENT)
    assert "selfLink" not in result["metadata"]


def test_removes_status():
    result = normalize(FULL_DEPLOYMENT)
    assert "status" not in result


def test_removes_last_applied_annotation():
    result = normalize(FULL_DEPLOYMENT)
    annotations = result["metadata"].get("annotations", {})
    assert "kubectl.kubernetes.io/last-applied-configuration" not in annotations


def test_preserves_user_annotations():
    result = normalize(FULL_DEPLOYMENT)
    assert result["metadata"]["annotations"]["my-team/owner"] == "platform"


def test_preserves_spec():
    result = normalize(FULL_DEPLOYMENT)
    assert result["spec"]["replicas"] == 3


def test_does_not_mutate_original():
    original = {"metadata": {"resourceVersion": "99", "name": "x"}, "spec": {}}
    normalize(original)
    assert original["metadata"]["resourceVersion"] == "99"


def test_extra_ignore_strips_field():
    result = normalize(FULL_DEPLOYMENT, extra_ignore=["spec.replicas"])
    assert "replicas" not in result["spec"]


def test_extra_ignore_nested():
    obj = {"metadata": {"name": "x"}, "spec": {"template": {"spec": {"serviceAccount": "default"}}}}
    result = normalize(obj, extra_ignore=["spec.template.spec.serviceAccount"])
    assert "serviceAccount" not in result["spec"]["template"]["spec"]
