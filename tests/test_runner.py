from __future__ import annotations

import sys

import pytest

from quaddeploy.runner import CommandError, run


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_returns_output_and_exit_code() -> None:
    result = run(_python("import sys; print('out'); print('err', file=sys.stderr)"))

    assert (result.returncode, result.stdout, result.stderr) == (0, "out\n", "err\n")


def test_run_raises_on_a_failing_command() -> None:
    with pytest.raises(CommandError, match="boom") as raised:
        run(_python("import sys; sys.exit('boom')"))

    assert raised.value.result.returncode == 1


def test_run_returns_a_failure_when_check_is_off() -> None:
    assert run(_python("import sys; sys.exit(3)"), check=False).returncode == 3


def test_run_raises_for_a_missing_program_even_when_check_is_off() -> None:
    with pytest.raises(CommandError) as raised:
        run(["quaddeploy-no-such-program"], check=False)

    assert raised.value.result.returncode == 127


def test_commands_cannot_prompt() -> None:
    code = "import os, sys; print(os.environ['GIT_TERMINAL_PROMPT'], repr(sys.stdin.read()))"

    assert run(_python(code)).stdout == "0 ''\n"
