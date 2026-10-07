from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from quaddeploy.config import Config, HealthConfig, VerifyConfig
from quaddeploy.runner import CommandError, Result

COMMIT = "abc123de" * 5
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def cosign_output(digest: str) -> Result:
    """Return what a passing `cosign verify` prints for an image with this digest."""
    payload = [{"critical": {"image": {"docker-manifest-digest": digest}}}]
    return Result(0, json.dumps(payload), "")


class FakeRunner:
    """Record every command. Return a canned result when an argument matches a key.

    A list of results is used up one call at a time, and its last entry repeats.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.results: dict[str, Result | list[Result]] = {
            "rev-parse": Result(0, f"{COMMIT}\n", ""),
            "cosign": cosign_output(DIGEST),
            "is-active": Result(0, "active\n", ""),
        }

    def __call__(self, argv: Sequence[str], *, check: bool = True) -> Result:
        args = list(argv)
        self.calls.append(args)
        for token, canned in self.results.items():
            if token not in args:
                continue
            if isinstance(canned, list):
                result = canned.pop(0) if len(canned) > 1 else canned[0]
            else:
                result = canned
            if check and result.returncode != 0:
                raise CommandError(args, result)
            return result
        return Result(0, "", "")

    def systemctl(self) -> list[list[str]]:
        """Return the arguments of each systemctl call that changes state."""
        calls = [call[2:] for call in self.calls if call[0] == "systemctl"]
        return [call for call in calls if call[0] != "is-active"]

    def ran(self, program: str) -> list[list[str]]:
        return [call for call in self.calls if call[0] == program]


@pytest.fixture
def fake_run() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """A config whose repo clone already exists, so the fake git has files to serve."""
    state_dir = tmp_path / "state"
    (state_dir / "repo" / ".git").mkdir(parents=True)
    (state_dir / "repo" / "deploy").mkdir()
    return Config(
        repo_url="https://git.example.com/alice/app.git",
        branch="main",
        deploy_path="deploy",
        state_dir=state_dir,
        unit_dir=tmp_path / "units",
        verify=VerifyConfig(key=Path("/etc/quaddeploy/cosign.pub")),
        health=HealthConfig(timeout=5, interval=0),
    )
