"""Tests for the recursive diff engine."""

import pytest
from gitops_drift.diff_engine import compute_diff


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
