"""Tests for CLI argument handling."""

import sys

import pytest

from gitops_drift import main as main_module
from gitops_drift.main import parse_args


def test_dry_run_defaults_on(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gitops-drift", "--manifests", "examples/desired"])

    args = parse_args()

    assert args.dry_run is True


def test_no_dry_run_toggles_dry_run_off(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["gitops-drift", "--manifests", "examples/desired", "--no-dry-run"],
    )

    args = parse_args()

    assert args.dry_run is False


def test_output_json_arg(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["gitops-drift", "--manifests", "examples/desired", "--output", "json"],
    )

    args = parse_args()

    assert args.output == "json"


def test_remediate_takes_precedence_over_dry_run(monkeypatch):
    captured = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["gitops-drift", "--manifests", "examples/desired", "--dry-run", "--remediate", "--once"],
    )
    monkeypatch.setattr(main_module, "load_kube_config", lambda _kubeconfig: None)
    monkeypatch.setattr(main_module, "run_once", lambda cfg: captured.append(cfg) or [])

    main_module.main()

    assert captured[0].remediate is True
    assert captured[0].dry_run is False


# ---------------------------------------------------------------------------
# --fail-on-drift tests
# ---------------------------------------------------------------------------

def test_fail_on_drift_flag_parsed(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gitops-drift", "--manifests", ".", "--fail-on-drift"])
    args = parse_args()
    assert args.fail_on_drift is True


def test_fail_on_drift_default_false(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gitops-drift", "--manifests", "."])
    args = parse_args()
    assert args.fail_on_drift is False


def test_fail_on_drift_exits_nonzero_when_drift_found(monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["gitops-drift", "--manifests", "examples/desired", "--dry-run", "--once", "--fail-on-drift"],
    )
    monkeypatch.setattr(main_module, "load_kube_config", lambda _: None)
    # Simulate drift detected: run_once returns one entry.
    monkeypatch.setattr(main_module, "run_once", lambda cfg: [{"kind": "Deployment"}])

    with pytest.raises(SystemExit) as exc:
        main_module.main()
    assert exc.value.code == 1


def test_fail_on_drift_does_not_exit_when_no_drift(monkeypatch):
    monkeypatch.setattr(
        sys, "argv",
        ["gitops-drift", "--manifests", "examples/desired", "--dry-run", "--once", "--fail-on-drift"],
    )
    monkeypatch.setattr(main_module, "load_kube_config", lambda _: None)
    monkeypatch.setattr(main_module, "run_once", lambda cfg: [])

    # Should complete without raising SystemExit.
    main_module.main()


# ---------------------------------------------------------------------------
# --interval validation tests
# ---------------------------------------------------------------------------

def test_interval_zero_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gitops-drift", "--manifests", ".", "--interval", "0"])
    with pytest.raises(SystemExit) as exc:
        parse_args()
    assert exc.value.code == 2


def test_interval_negative_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gitops-drift", "--manifests", ".", "--interval", "-10"])
    with pytest.raises(SystemExit) as exc:
        parse_args()
    assert exc.value.code == 2


def test_interval_positive_accepted(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gitops-drift", "--manifests", ".", "--interval", "30"])
    args = parse_args()
    assert args.interval == 30
