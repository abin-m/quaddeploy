"""Run external commands through one function so tests can replace it."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


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
        super().__init__(f"{' '.join(self.argv)} failed ({result.returncode}): {detail}")


class Runner(Protocol):
    def __call__(self, argv: Sequence[str], *, check: bool = True) -> Result: ...


def run(argv: Sequence[str], *, check: bool = True) -> Result:
    """Run a command and return its output. Raise CommandError on failure if check is set."""
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise CommandError(argv, Result(127, "", str(exc))) from exc
    result = Result(proc.returncode, proc.stdout, proc.stderr)
    if check and result.returncode != 0:
        raise CommandError(argv, result)
    return result
