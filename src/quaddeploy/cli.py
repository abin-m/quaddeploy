"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from quaddeploy import __version__, config, notify, preflight, reconcile, release, status, timer
from quaddeploy.gitrepo import UntrustedCommitError
from quaddeploy.health import HealthError
from quaddeploy.release import UnmanagedFileError
from quaddeploy.runner import CommandError, Runner, run
from quaddeploy.verify import VerifyError

log = logging.getLogger("quaddeploy")

_ERRORS = (
    CommandError,
    UntrustedCommitError,
    reconcile.ReconcileError,
    UnmanagedFileError,
    VerifyError,
    HealthError,
    OSError,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quaddeploy", description="Deploy Quadlet units to this host from a Git repo."
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help="path to config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="fetch the repo and deploy any change")
    sync.add_argument("--force", action="store_true", help="retry a release that failed before")
    commands.add_parser("status", help="print the result of the last run")
    commands.add_parser("rollback", help="return to the release before the current one")
    commands.add_parser("verify", help="check the signatures of the repo's images")
    commands.add_parser("preflight", help="check that this host is ready, and say what is missing")
    commands.add_parser("notify", help="send a test message to the configured webhook")
    init = commands.add_parser("init", help="install the systemd timer that runs sync")
    init.add_argument(
        "--schedule",
        type=timer.check_schedule,
        default=timer.DEFAULT_SCHEDULE,
        help="systemd OnCalendar expression (default: every two minutes)",
    )
    return parser


def _recorded(cfg: config.Config, action: Callable[[], int]) -> int:
    """Run action. Log and record any expected error, and return exit code 1 for it."""
    try:
        return action()
    except _ERRORS as exc:
        log.error("%s", exc)
        status.write(cfg.state_dir, "failed", error=str(exc))
        return 1


def _sync(cfg: config.Config, runner: Runner, force: bool) -> int:
    outcome = reconcile.sync(cfg, runner, force=force)
    if outcome.error:
        log.error("%s: %s", outcome.result, outcome.error)
        return 1
    log.info("%s: release %s, commit %s", outcome.result, outcome.release, outcome.commit)
    return 0


def _rollback(cfg: config.Config, runner: Runner) -> int:
    outcome = reconcile.rollback(cfg, runner)
    log.info("rolled back to release %s", outcome.release)
    return 0


def _verify(cfg: config.Config, runner: Runner) -> int:
    try:
        want = reconcile.desired(cfg, runner)
    except _ERRORS as exc:
        log.error("%s", exc)
        return 1
    for ref, pinned in want.images.items():
        print(f"{ref} -> {pinned}")
    return 0


def _init(runner: Runner, config_path: Path, schedule: str) -> int:
    executable = Path(shutil.which("quaddeploy") or sys.argv[0]).resolve()
    systemd_dir = config.systemd_user_dir()
    try:
        timer.install(runner, systemd_dir, executable, config_path.resolve(), schedule)
    except (CommandError, OSError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    print(f"Installed {timer.SERVICE} and {timer.TIMER} in {systemd_dir}.")
    print("To keep the timer running while you are logged out, run:")
    print("  loginctl enable-linger")
    return 0


def _notify_test(cfg: config.Config) -> int:
    if cfg.notify_url is None:
        log.error("no webhook is configured. Set url in the [notify] section of the config")
        return 1
    try:
        notify.send(cfg.notify_url, notify.payload("test", {}))
    except notify.NotifyError as exc:
        log.error("the webhook did not accept the message: %s", exc)
        return 1
    print("Sent a test notification.")
    return 0


def _preflight(runner: Runner, config_path: Path) -> int:
    checks = preflight.run_checks(runner, config_path)
    for check in checks:
        print(f"{check.level:<4}  {check.summary}")
        if check.fix:
            print(f"      {check.fix}")
    failed = sum(check.level == preflight.FAIL for check in checks)
    print()
    if failed:
        print(f"{failed} problem{'s' if failed > 1 else ''} to fix before this host can deploy.")
        return 1
    print("This host is ready. Next: quaddeploy verify")
    return 0


def _status(cfg: config.Config) -> int:
    record = status.read(cfg.state_dir)
    if record is None:
        print("quaddeploy has not run yet")
        return 1
    current = release.current(cfg.state_dir)
    record["drift"] = release.drift(current, cfg.unit_dir) if current else []
    print(json.dumps(record, indent=2))
    return 0 if record["result"] in ("deployed", "unchanged") else 1


def main(argv: Sequence[str] | None = None, runner: Runner = run) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    config_path = args.config or config.default_config_path()
    # Preflight reports a bad config itself, with the other checks.
    if args.command == "preflight":
        return _preflight(runner, config_path)
    try:
        cfg = config.load(config_path)
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2
    if args.command == "init":
        return _init(runner, config_path, args.schedule)
    if args.command == "sync":
        previous = status.read(cfg.state_dir)
        code = _recorded(cfg, lambda: _sync(cfg, runner, args.force))
        notify.report(cfg.notify_url, previous, status.read(cfg.state_dir))
        return code
    if args.command == "notify":
        return _notify_test(cfg)
    if args.command == "rollback":
        return _recorded(cfg, lambda: _rollback(cfg, runner))
    if args.command == "verify":
        return _verify(cfg, runner)
    return _status(cfg)


if __name__ == "__main__":
    sys.exit(main())
