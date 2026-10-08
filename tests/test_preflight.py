from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from quaddeploy import cli, preflight
from quaddeploy.runner import CommandError, Result

COSIGN_BANNER = (
    "cosign: A tool for Container Signing\n\nGitVersion:    v3.1.3\nPlatform: linux/arm64\n"
)


class Tools:
    """A host whose commands print what the real ones do. None means not installed."""

    def __init__(self) -> None:
        self.outputs: dict[str, Result | None] = {
            "git --version": Result(0, "git version 2.52.0\n", ""),
            "podman --version": Result(0, "podman version 5.8.2\n", ""),
            "cosign version": Result(0, COSIGN_BANNER, ""),
            "systemctl --user is-system-running": Result(0, "running\n", ""),
            "loginctl show-user": Result(0, "Linger=yes\n", ""),
            "git ls-remote": Result(0, "abc123\trefs/heads/main\n", ""),
            "systemctl --user is-enabled": Result(0, "enabled\n", ""),
        }

    def __call__(self, argv: Sequence[str], *, check: bool = True) -> Result:
        line = " ".join(argv)
        for prefix, result in self.outputs.items():
            if line.startswith(prefix):
                if result is None:
                    raise CommandError(argv, Result(127, "", "not found"))
                return result
        raise AssertionError(f"unexpected command: {line}")


@pytest.fixture
def tools() -> Tools:
    return Tools()


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    signers = tmp_path / "allowed_signers"
    signers.write_text("alice@example.com ssh-ed25519 AAAA\n")
    path = tmp_path / "config.toml"
    path.write_text(
        '[repo]\nurl = "https://git.example.com/alice/app.git"\n'
        f'allowed_signers = "{signers}"\n'
        "[verify]\n"
        'certificate_identity = "https://git.example.com/alice/app/workflow@refs/heads/main"\n'
        'certificate_oidc_issuer = "https://token.example.com"\n'
    )
    return path


def _levels(checks: list[preflight.Check]) -> list[str]:
    return [check.level for check in checks]


def _find(checks: list[preflight.Check], text: str) -> preflight.Check:
    return next(check for check in checks if text in check.summary)


def test_a_ready_host_passes_every_check(tools: Tools, config_file: Path) -> None:
    checks = preflight.run_checks(tools, config_file)

    assert set(_levels(checks)) == {preflight.OK}
    assert [check.summary for check in checks[:3]] == ["git 2.52.0", "podman 5.8.2", "cosign 3.1.3"]


@pytest.mark.parametrize("tool", ["git", "podman", "cosign"])
def test_a_missing_tool_fails_with_a_fix(tools: Tools, config_file: Path, tool: str) -> None:
    key = next(k for k in tools.outputs if k.startswith(f"{tool} ") and "ls-remote" not in k)
    tools.outputs[key] = None
    if tool == "git":
        tools.outputs["git ls-remote"] = None

    check = _find(preflight.run_checks(tools, config_file), f"{tool} is not installed")

    assert check.level == preflight.FAIL
    assert check.fix


def test_an_old_podman_fails(tools: Tools, config_file: Path) -> None:
    tools.outputs["podman --version"] = Result(0, "podman version 4.3.1\n", "")

    check = _find(preflight.run_checks(tools, config_file), "podman")

    assert check.level == preflight.FAIL
    assert "4.4.0 or later" in check.summary


def test_an_old_cosign_fails(tools: Tools, config_file: Path) -> None:
    tools.outputs["cosign version"] = Result(0, "GitVersion:    v1.13.6\n", "")

    assert _find(preflight.run_checks(tools, config_file), "cosign").level == preflight.FAIL


def test_an_unreadable_version_is_only_a_warning(tools: Tools, config_file: Path) -> None:
    tools.outputs["cosign version"] = Result(0, "development build\n", "")

    assert _find(preflight.run_checks(tools, config_file), "cosign").level == preflight.WARN


def test_no_user_session_fails(tools: Tools, config_file: Path) -> None:
    tools.outputs["systemctl --user is-system-running"] = Result(
        1, "", "Failed to connect to bus: No medium found\n"
    )

    check = _find(preflight.run_checks(tools, config_file), "no systemd user session")

    assert check.level == preflight.FAIL
    assert "SSH" in check.fix


def test_a_degraded_session_is_accepted(tools: Tools, config_file: Path) -> None:
    tools.outputs["systemctl --user is-system-running"] = Result(1, "degraded\n", "")

    assert _find(preflight.run_checks(tools, config_file), "session").level == preflight.OK


def test_no_systemd_fails(tools: Tools, config_file: Path) -> None:
    for key in ("systemctl --user is-system-running", "systemctl --user is-enabled"):
        tools.outputs[key] = None
    tools.outputs["loginctl show-user"] = None

    checks = preflight.run_checks(tools, config_file)

    assert _find(checks, "systemd is not available").level == preflight.FAIL


def test_lingering_off_is_a_warning(tools: Tools, config_file: Path) -> None:
    tools.outputs["loginctl show-user"] = Result(0, "Linger=no\n", "")

    check = _find(preflight.run_checks(tools, config_file), "lingering is off")

    assert check.level == preflight.WARN
    assert "loginctl enable-linger" in check.fix


def test_a_missing_timer_is_a_warning(tools: Tools, config_file: Path) -> None:
    tools.outputs["systemctl --user is-enabled"] = Result(1, "", "not-found\n")

    assert _find(preflight.run_checks(tools, config_file), "timer is not").level == preflight.WARN


def test_a_missing_config_fails_and_skips_the_repo_check(tools: Tools, tmp_path: Path) -> None:
    checks = preflight.run_checks(tools, tmp_path / "absent.toml")

    assert _find(checks, "config file not found").level == preflight.FAIL
    assert not any("repo" in check.summary for check in checks)


def test_an_unreachable_repo_fails(tools: Tools, config_file: Path) -> None:
    tools.outputs["git ls-remote"] = Result(128, "", "fatal: repository not found\n")

    check = _find(preflight.run_checks(tools, config_file), "cannot read")

    assert check.level == preflight.FAIL
    assert "repository not found" in check.summary


def test_a_missing_branch_fails(tools: Tools, config_file: Path) -> None:
    tools.outputs["git ls-remote"] = Result(0, "", "")

    check = _find(preflight.run_checks(tools, config_file), "no branch named main")

    assert check.level == preflight.FAIL


def test_a_missing_public_key_fails(tools: Tools, tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[repo]\nurl = "https://git.example.com/alice/app.git"\n'
        f'[verify]\nkey = "{tmp_path / "cosign.pub"}"\n'
    )

    assert _find(preflight.run_checks(tools, path), "public key").level == preflight.FAIL


def test_command_exits_zero_and_points_to_the_next_step(
    tools: Tools, config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--config", str(config_file), "preflight"], tools) == 0

    out = capsys.readouterr().out
    assert "ok    podman 5.8.2" in out
    assert "Next: quaddeploy verify" in out


def test_command_exits_one_and_prints_each_fix(
    tools: Tools, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tools.outputs["cosign version"] = None

    assert cli.main(["--config", str(tmp_path / "absent.toml"), "preflight"], tools) == 1

    out = capsys.readouterr().out
    assert "FAIL  cosign is not installed" in out
    assert "      Install it: https://docs.sigstore.dev" in out
    assert "2 problems to fix" in out


def test_unverified_commits_are_a_warning(tools: Tools, config_file: Path) -> None:
    config_file.write_text(
        "".join(line for line in config_file.read_text().splitlines(True) if "signers" not in line)
    )

    check = _find(preflight.run_checks(tools, config_file), "commits are not verified")

    assert check.level == preflight.WARN
    assert "allowed_signers" in check.fix


def test_a_missing_allowed_signers_file_fails(tools: Tools, config_file: Path) -> None:
    (config_file.parent / "allowed_signers").unlink()

    check = _find(preflight.run_checks(tools, config_file), "allowed signers file")

    assert check.level == preflight.FAIL
