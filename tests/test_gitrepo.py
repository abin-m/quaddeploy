from __future__ import annotations

from pathlib import Path

import pytest
from conftest import COMMIT, FakeRunner

from quaddeploy import gitrepo
from quaddeploy.runner import Result

URL = "https://git.example.com/alice/app.git"


def test_update_clones_when_the_folder_is_new(tmp_path: Path, fake_run: FakeRunner) -> None:
    dest = tmp_path / "state" / "repo"

    commit = gitrepo.update(fake_run, URL, "main", dest)

    assert commit == COMMIT
    assert fake_run.calls[0] == ["git", "clone", "--no-checkout", URL, str(dest)]
    assert ["git", "-C", str(dest), "fetch", "--prune", "origin", "main"] in fake_run.calls


def test_update_skips_the_clone_when_the_folder_exists(
    tmp_path: Path, fake_run: FakeRunner
) -> None:
    (tmp_path / ".git").mkdir()

    gitrepo.update(fake_run, URL, "main", tmp_path)

    assert not any("clone" in call for call in fake_run.calls)
    assert ["git", "-C", str(tmp_path), "checkout", "--force", "--detach", COMMIT] in fake_run.calls


def _verify_calls(fake_run: FakeRunner) -> list[list[str]]:
    return [call for call in fake_run.calls if "verify-commit" in call]


def test_update_does_not_verify_commits_by_default(tmp_path: Path, fake_run: FakeRunner) -> None:
    (tmp_path / ".git").mkdir()

    gitrepo.update(fake_run, URL, "main", tmp_path)

    assert _verify_calls(fake_run) == []


def test_update_verifies_the_fetched_commit_before_checkout(
    tmp_path: Path, fake_run: FakeRunner
) -> None:
    (tmp_path / ".git").mkdir()
    signers = tmp_path / "allowed_signers"

    gitrepo.update(fake_run, URL, "main", tmp_path, signers)

    (verify,) = _verify_calls(fake_run)
    assert f"gpg.ssh.allowedSignersFile={signers}" in verify
    assert "gpg.openpgp.program=false" in verify
    assert verify[-2:] == ["verify-commit", COMMIT]
    order = [call[-1] if "verify-commit" not in call else "verify" for call in fake_run.calls]
    assert order.index("verify") < order.index(COMMIT)


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        ("", "it has no signature"),
        ('Good "git" signature\nNo principal matched.\n', "No principal"),
    ],
)
def test_update_refuses_an_untrusted_commit_and_checks_nothing_out(
    tmp_path: Path, fake_run: FakeRunner, stderr: str, reason: str
) -> None:
    (tmp_path / ".git").mkdir()
    fake_run.results["verify-commit"] = Result(1, "", stderr)

    with pytest.raises(gitrepo.UntrustedCommitError, match=reason):
        gitrepo.update(fake_run, URL, "main", tmp_path, tmp_path / "allowed_signers")

    assert not any("checkout" in call for call in fake_run.calls)


def test_base_command_is_plain_git_without_a_key() -> None:
    assert gitrepo.base_command() == ["git"]


def test_base_command_quotes_the_key_for_the_shell_that_git_starts() -> None:
    command = gitrepo.base_command(Path("/home/alice/my keys/deploy"))

    ssh = "ssh -i '/home/alice/my keys/deploy' -o IdentitiesOnly=yes -o BatchMode=yes"
    assert command == ["git", "-c", f"core.sshCommand={ssh}"]


def test_update_uses_the_deploy_key_for_every_git_command(
    tmp_path: Path, fake_run: FakeRunner
) -> None:
    key = tmp_path / "deploy_key"

    gitrepo.update(fake_run, URL, "main", tmp_path / "clone", ssh_key=key)

    base = gitrepo.base_command(key)
    assert all(call[:3] == base for call in fake_run.calls)
    assert fake_run.calls[0][3] == "clone"
