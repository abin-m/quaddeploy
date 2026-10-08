"""Run external commands through one function so tests can replace it."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@")


def redact(text: str) -> str:
    """Hide the user name, password or token written into an HTTP URL."""
    return _URL_CREDENTIALS.sub(r"\1***@", text)


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    """A command exited non-zero or could not be started."""

    def __init__(self, argv: Sequence[str], result: Result) -> None:
        self.argv = list(argv)
        self.result = result
        detail = result.stderr.strip() or result.stdout.strip()
        # This text reaches the journal, the status file and the webhook.
        super().__init__(redact(f"{' '.join(self.argv)} failed ({result.returncode}): {detail}"))


class Runner(Protocol):
    def __call__(self, argv: Sequence[str], *, check: bool = True) -> Result: ...


def run(
    argv: Sequence[str], *, check: bool = True, extra_env: Mapping[str, str] | None = None
) -> Result:
    """Run a command and return its output. Raise CommandError on failure if check is set."""
    # The agent runs unattended. A command that asks a question must fail, not wait.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(extra_env or {})}
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except FileNotFoundError as exc:
        raise CommandError(argv, Result(127, "", str(exc))) from exc
    result = Result(proc.returncode, proc.stdout, proc.stderr)
    if check and result.returncode != 0:
        raise CommandError(argv, result)
    return result


def with_env(extra_env: Mapping[str, str]) -> Runner:
    """Return a runner that adds extra_env to the environment of every command."""

    def runner(argv: Sequence[str], *, check: bool = True) -> Result:
        return run(argv, check=check, extra_env=extra_env)

    return runner
