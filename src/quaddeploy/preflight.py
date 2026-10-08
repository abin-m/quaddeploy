"""Check that the host has what sync needs, and say how to fix what is missing."""

from __future__ import annotations

import getpass
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from quaddeploy import config, timer
from quaddeploy.runner import CommandError, Runner

OK, WARN, FAIL = "ok", "warn", "FAIL"

_VERSION = r"(\d+)\.(\d+)(?:\.(\d+))?"
# States in which the user manager answers. A degraded session still runs units.
_SESSION_STATES = frozenset({"running", "degraded", "starting", "initializing"})

_INSTALL_COSIGN = "Install it: https://docs.sigstore.dev/cosign/system_config/installation/"


@dataclass(frozen=True)
class Check:
    level: str
    summary: str
    fix: str = ""


def _output(run: Runner, argv: Sequence[str]) -> str | None:
    """Return what the command printed, or None if the program is not installed."""
    try:
        result = run(argv, check=False)
    except CommandError:
        return None
    return f"{result.stdout}\n{result.stderr}".strip()


def _tool(
    run: Runner,
    name: str,
    argv: Sequence[str],
    minimum: tuple[int, int, int],
    fix: str,
    pattern: str = _VERSION,
) -> Check:
    """Check that a program is installed and at least the minimum version."""
    output = _output(run, argv)
    if output is None:
        return Check(FAIL, f"{name} is not installed", fix)
    match = re.search(pattern, output)
    if not match:
        return Check(WARN, f"{name} is installed, but its version could not be read")
    version = (int(match[1]), int(match[2]), int(match[3] or 0))
    text = ".".join(str(part) for part in version)
    if version < minimum:
        needed = ".".join(str(part) for part in minimum)
        return Check(FAIL, f"{name} {text} is too old. It must be {needed} or later", fix)
    return Check(OK, f"{name} {text}")


def _session(run: Runner) -> Check:
    output = _output(run, ["systemctl", "--user", "is-system-running"])
    if output is None:
        return Check(FAIL, "systemd is not available", "QuadDeploy needs Linux with systemd.")
    state = output.splitlines()[0].strip() if output else ""
    if state in _SESSION_STATES:
        return Check(OK, f"systemd user session is {state}")
    return Check(
        FAIL,
        "there is no systemd user session",
        "Log in as the deploy user directly, for example over SSH. 'su' and 'sudo -u' "
        "do not start one.",
    )


def _linger(run: Runner) -> Check:
    output = _output(run, ["loginctl", "show-user", getpass.getuser(), "--property=Linger"])
    if output is not None and "Linger=yes" in output:
        return Check(OK, "lingering is on")
    return Check(
        WARN,
        "lingering is off, so services and the timer stop when you log out",
        "Run: loginctl enable-linger",
    )


def _timer(run: Runner) -> Check:
    output = _output(run, ["systemctl", "--user", "is-enabled", timer.TIMER])
    if output is not None and output.splitlines()[:1] == ["enabled"]:
        return Check(OK, "the sync timer is installed")
    return Check(
        WARN,
        "the sync timer is not installed",
        "After a first 'quaddeploy sync', run: quaddeploy init",
    )


def _repo(run: Runner, cfg: config.Config) -> Check | None:
    """Check that the config repo can be read and has the branch. None if git is missing."""
    try:
        result = run(["git", "ls-remote", "--heads", cfg.repo_url, cfg.branch], check=False)
    except CommandError:
        return None
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or ["git gave no reason"])[-1]
        return Check(
            FAIL,
            f"cannot read {cfg.repo_url}: {reason}",
            "Check repo.url. A private repo needs Git credentials on this host.",
        )
    if not result.stdout.strip():
        return Check(FAIL, f"{cfg.repo_url} has no branch named {cfg.branch}", "Check repo.branch.")
    return Check(OK, f"the repo is reachable and has branch {cfg.branch}")


def _config(run: Runner, config_path: Path) -> list[Check]:
    try:
        cfg = config.load(config_path)
    except config.ConfigError as exc:
        return [Check(FAIL, str(exc), "See examples/config.toml in the QuadDeploy repo.")]
    checks = [Check(OK, f"config {config_path} is valid")]
    if cfg.verify.key is not None and not cfg.verify.key.is_file():
        checks.append(
            Check(
                FAIL,
                f"public key {cfg.verify.key} does not exist",
                "Copy the cosign public key there, or correct verify.key.",
            )
        )
    repo = _repo(run, cfg)
    if repo is not None:
        checks.append(repo)
    return checks


def run_checks(run: Runner, config_path: Path) -> list[Check]:
    """Run every check and return the results in the order a user should fix them."""
    return [
        _tool(
            run, "git", ["git", "--version"], (0, 0, 0), "Install git with your package manager."
        ),
        _tool(
            run,
            "podman",
            ["podman", "--version"],
            (4, 4, 0),
            "Install Podman 4.4 or later. Quadlet is not in earlier versions.",
        ),
        _tool(
            run,
            "cosign",
            ["cosign", "version"],
            (2, 0, 1),
            _INSTALL_COSIGN,
            pattern=rf"GitVersion:\s*v?{_VERSION}",
        ),
        _session(run),
        _linger(run),
        *_config(run, config_path),
        _timer(run),
    ]
