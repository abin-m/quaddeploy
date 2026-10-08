from __future__ import annotations

import sys

import pytest

from quaddeploy.runner import CommandError, Result, redact, run, with_env


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


def test_with_env_adds_variables_for_the_command() -> None:
    runner = with_env({"REGISTRY_AUTH_FILE": "/etc/quaddeploy/auth.json"})
    code = "import os; print(os.environ['REGISTRY_AUTH_FILE'], os.environ['GIT_TERMINAL_PROMPT'])"

    assert runner(_python(code)).stdout == "/etc/quaddeploy/auth.json 0\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://alice:s3cret@git.example.com/a.git", "https://***@git.example.com/a.git"),
        ("http://tokenvalue@git.example.com/a.git", "http://***@git.example.com/a.git"),
        ("https://git.example.com/a.git", "https://git.example.com/a.git"),
        ("ssh://git@git.example.com/a.git", "ssh://git@git.example.com/a.git"),
        ("mail alice@example.com about it", "mail alice@example.com about it"),
    ],
)
def test_redact_hides_credentials_in_http_urls(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_command_error_does_not_show_a_token_from_the_url() -> None:
    url = "https://x-access-token:s3cret@git.example.com/alice/app.git"
    error = CommandError(["git", "clone", url], Result(128, "", f"fatal: unable to access '{url}'"))

    assert "s3cret" not in str(error)
    assert str(error).count("https://***@git.example.com") == 2
