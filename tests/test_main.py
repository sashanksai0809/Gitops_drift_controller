"""Tests for CLI argument handling."""

import sys

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
    monkeypatch.setattr(main_module, "run_once", lambda cfg: captured.append(cfg))

    main_module.main()

    assert captured[0].remediate is True
    assert captured[0].dry_run is False
