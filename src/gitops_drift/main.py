"""Entry point for the GitOps drift detection controller."""

import argparse
import logging
import signal
import sys
import threading

from .config import ControllerConfig
from .kubernetes_client import load_kube_config
from .reconciler import run_once


def _positive_int(value: str) -> int:
    """argparse type that rejects zero and negative intervals."""
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return ivalue


def parse_args():
    parser = argparse.ArgumentParser(
        description="GitOps Drift Detection Controller -- compares local manifests against live cluster state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-shot dry-run against ./examples/desired
  gitops-drift --manifests ./examples/desired --namespace default --dry-run --once

  # CI pipeline: fail if drift exists
  gitops-drift --manifests ./examples/desired --dry-run --once --fail-on-drift

  # Continuous loop every 60s in dry-run mode
  gitops-drift --manifests ./examples/desired --namespace default --dry-run --interval 60

  # One-shot with remediation enabled
  gitops-drift --manifests ./examples/desired --namespace default --remediate --once
        """,
    )

    parser.add_argument("--manifests", required=True, help="Path to directory containing desired-state manifests")
    parser.add_argument("--namespace", default="default", help="Default namespace (used when manifest has none)")
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Report drift without applying changes (default: on)",
    )
    parser.add_argument("--remediate", action="store_true", default=False, help="Re-apply desired state when drift is detected")
    parser.add_argument("--once", action="store_true", default=False, help="Run a single reconciliation cycle and exit")
    parser.add_argument(
        "--interval",
        type=_positive_int,
        default=60,
        help="Reconciliation interval in seconds, must be > 0 (default: 60)",
    )
    parser.add_argument("--kubeconfig", default=None, help="Path to kubeconfig file (defaults to ~/.kube/config)")
    parser.add_argument("--ignore-fields", default="", help="Comma-separated extra field paths to ignore globally")
    parser.add_argument("--output", choices=["text", "json"], default="text", help="Report output format (default: text)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        default=False,
        help="Exit with status 1 if any drift is detected (useful in CI pipelines)",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # --remediate takes precedence because it is the explicit write mode.
    dry_run = args.dry_run and not args.remediate

    cfg = ControllerConfig(
        manifests_dir=args.manifests,
        namespace=args.namespace,
        dry_run=dry_run,
        remediate=args.remediate,
        interval=args.interval,
        once=args.once,
        kubeconfig=args.kubeconfig,
        output=args.output,
        extra_ignore_fields=[f.strip() for f in args.ignore_fields.split(",") if f.strip()],
        fail_on_drift=args.fail_on_drift,
    )

    try:
        load_kube_config(cfg.kubeconfig)
    except Exception as e:
        logging.critical("Could not load Kubernetes config: %s", e)
        sys.exit(1)

    if cfg.remediate:
        logging.warning("Remediation mode is ACTIVE -- drift will be corrected automatically")

    if cfg.once:
        entries = run_once(cfg)
        if cfg.fail_on_drift and entries:
            logging.warning("Drift detected and --fail-on-drift is set: exiting with status 1")
            sys.exit(1)
        return

    stop_event = threading.Event()

    def _request_shutdown(signum, _frame):
        logging.info("Received signal %s, shutting down after current cycle", signum)
        stop_event.set()

    # Handle both SIGTERM (container orchestrator) and SIGINT (Ctrl-C).
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    any_drift_seen = False
    logging.info("Starting reconciliation loop (interval=%ds)", cfg.interval)
    while not stop_event.is_set():
        try:
            entries = run_once(cfg)
            if entries:
                any_drift_seen = True
        except Exception as e:
            # Log and continue -- a transient API error should not kill the loop.
            logging.error("Reconciliation cycle failed: %s", e)
        stop_event.wait(cfg.interval)

    if cfg.fail_on_drift and any_drift_seen:
        logging.warning("Drift was detected during this run and --fail-on-drift is set: exiting with status 1")
        sys.exit(1)


if __name__ == "__main__":
    main()
