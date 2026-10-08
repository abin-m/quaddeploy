from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

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


class Webhook:
    """A local HTTP server that records the JSON bodies posted to it."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.status = 204
        hook = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                hook.received.append(json.loads(body))
                self.send_response(hook.status)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        self._thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def events(self) -> list[str]:
        return [body["event"] for body in self.received]


@pytest.fixture
def webhook() -> Iterator[Webhook]:
    hook = Webhook()
    hook.start()
    yield hook
    hook.stop()
