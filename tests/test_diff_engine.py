"""Tests for the recursive diff engine."""

import pytest
from gitops_drift.diff_engine import compute_diff, _is_named_list


def test_no_diff_identical():
    a = {"spec": {"replicas": 2, "image": "nginx:1.21"}}
    assert compute_diff(a, a) == []


def test_detects_scalar_change():
    desired = {"spec": {"replicas": 3}}
    live = {"spec": {"replicas": 1}}
    diffs = compute_diff(desired, live)
    assert len(diffs) == 1
    assert diffs[0]["path"] == "spec.replicas"
    assert diffs[0]["desired"] == 3
    assert diffs[0]["live"] == 1


def test_detects_nested_change():
    desired = {"spec": {"template": {"spec": {"containers": [{"image": "nginx:1.21"}]}}}}
    live = {"spec": {"template": {"spec": {"containers": [{"image": "nginx:1.19"}]}}}}
    diffs = compute_diff(desired, live)
    assert any("image" in d["path"] for d in diffs)


def test_detects_missing_key_in_live():
    desired = {"spec": {"replicas": 2, "minReadySeconds": 10}}
    live = {"spec": {"replicas": 2}}
    diffs = compute_diff(desired, live)
    assert len(diffs) == 1
    assert diffs[0]["path"] == "spec.minReadySeconds"
    assert diffs[0]["live"] == "<missing>"


def test_extra_keys_in_live_are_not_flagged():
    # Kubernetes injects default fields like imagePullPolicy -- these should not
    # show as drift since they are not in the desired manifest.
    desired = {"spec": {"containers": [{"name": "app", "image": "nginx"}]}}
    live = {"spec": {"containers": [{"name": "app", "image": "nginx", "imagePullPolicy": "Always"}]}}
    diffs = compute_diff(desired, live)
    assert diffs == []


def test_detects_list_element_change():
    desired = {"spec": {"ports": [{"containerPort": 80}]}}
    live = {"spec": {"ports": [{"containerPort": 8080}]}}
    diffs = compute_diff(desired, live)
    assert len(diffs) == 1
    assert diffs[0]["desired"] == 80
    assert diffs[0]["live"] == 8080


def test_detects_missing_list_element():
    desired = {"spec": {"containers": [{"name": "a"}, {"name": "b"}]}}
    live = {"spec": {"containers": [{"name": "a"}]}}
    diffs = compute_diff(desired, live)
    assert any("<missing>" in str(d["live"]) for d in diffs)


def test_structured_diff_fields():
    desired = {"spec": {"replicas": 5}}
    live = {"spec": {"replicas": 1}}
    diffs = compute_diff(desired, live)
    assert "path" in diffs[0]
    assert "desired" in diffs[0]
    assert "live" in diffs[0]


def test_empty_objects():
    assert compute_diff({}, {}) == []


def test_top_level_scalar_change():
    diffs = compute_diff("nginx:1.21", "nginx:1.19", path="image")
    assert len(diffs) == 1
    assert diffs[0]["desired"] == "nginx:1.21"


# ---------------------------------------------------------------------------
# Named-list (semantic) matching tests
# ---------------------------------------------------------------------------

def test_named_list_reorder_is_not_drift():
    """Containers swapped in order should produce zero diffs."""
    desired = {"spec": {"containers": [
        {"name": "app", "image": "nginx:1.25"},
        {"name": "sidecar", "image": "envoy:v1.0"},
    ]}}
    live = {"spec": {"containers": [
        {"name": "sidecar", "image": "envoy:v1.0"},
        {"name": "app", "image": "nginx:1.25"},
    ]}}
    assert compute_diff(desired, live) == []


def test_named_list_detects_value_change_by_name():
    """A changed image in one container should be reported under that container's name."""
    desired = {"spec": {"containers": [
        {"name": "app", "image": "nginx:1.25"},
        {"name": "sidecar", "image": "envoy:v1.0"},
    ]}}
    live = {"spec": {"containers": [
        {"name": "sidecar", "image": "envoy:v1.0"},
        {"name": "app", "image": "nginx:1.19"},
    ]}}
    diffs = compute_diff(desired, live)
    assert len(diffs) == 1
    assert "name=app" in diffs[0]["path"]
    assert "image" in diffs[0]["path"]
    assert diffs[0]["desired"] == "nginx:1.25"
    assert diffs[0]["live"] == "nginx:1.19"


def test_named_list_detects_missing_container():
    """A container present in desired but absent from live should be reported as missing."""
    desired = {"spec": {"containers": [
        {"name": "app", "image": "nginx"},
        {"name": "sidecar", "image": "envoy"},
    ]}}
    live = {"spec": {"containers": [
        {"name": "app", "image": "nginx"},
    ]}}
    diffs = compute_diff(desired, live)
    assert len(diffs) == 1
    assert "name=sidecar" in diffs[0]["path"]
    assert diffs[0]["live"] == "<missing>"


def test_named_list_extra_live_container_not_flagged():
    """A container injected by an admission webhook (live-only) should not appear as drift."""
    desired = {"spec": {"containers": [{"name": "app", "image": "nginx"}]}}
    live = {"spec": {"containers": [
        {"name": "app", "image": "nginx"},
        {"name": "injected-sidecar", "image": "istio/proxy"},
    ]}}
    assert compute_diff(desired, live) == []


def test_unnamed_list_falls_back_to_positional():
    """Lists without a 'name' key are still compared positionally."""
    desired = {"spec": {"ports": [{"containerPort": 80}, {"containerPort": 443}]}}
    live = {"spec": {"ports": [{"containerPort": 8080}, {"containerPort": 443}]}}
    diffs = compute_diff(desired, live)
    assert len(diffs) == 1
    assert diffs[0]["desired"] == 80
    assert diffs[0]["live"] == 8080


def test_is_named_list_true_for_container_list():
    containers = [{"name": "app", "image": "nginx"}, {"name": "sidecar", "image": "envoy"}]
    assert _is_named_list(containers) is True


def test_is_named_list_false_for_mixed_list():
    mixed = [{"name": "app"}, {"containerPort": 80}]
    assert _is_named_list(mixed) is False


def test_is_named_list_false_for_empty():
    assert _is_named_list([]) is False


def test_named_list_multi_field_drift_reported_per_field():
    """Multiple drifted fields within a named container are each reported separately."""
    desired = {"spec": {"containers": [{"name": "app", "image": "nginx:1.25", "imagePullPolicy": "Always"}]}}
    live = {"spec": {"containers": [{"name": "app", "image": "nginx:1.19", "imagePullPolicy": "IfNotPresent"}]}}
    diffs = compute_diff(desired, live)
    paths = [d["path"] for d in diffs]
    assert any("image" in p for p in paths)
    assert any("imagePullPolicy" in p for p in paths)
    assert all("name=app" in p for p in paths)
