"""Commit verification against real git and ssh-keygen, with no fake runner."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quaddeploy import gitrepo
from quaddeploy.runner import run

pytestmark = pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("ssh-keygen")), reason="needs git and ssh-keygen"
)

# The developer's own Git hooks and signing settings must not affect these commits.
GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "core.hooksPath="]


class Origin:
    """A config repo with one trusted signing key and one that is not trusted."""

    def __init__(self, root: Path) -> None:
        self.path = root / "origin"
        self.allowed_signers = root / "allowed_signers"
        self.trusted = self._key(root / "trusted")
        self.other = self._key(root / "other")
        self.allowed_signers.write_text(f"alice@example.com {self.trusted.read_text()}")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.path)], check=True)

    @staticmethod
    def _key(path: Path) -> Path:
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
        return path.with_suffix(".pub")

    def commit(self, content: str, key: Path | None = None) -> str:
        """Commit content as unit.container, signed with key if one is given."""
        (self.path / "unit.container").write_text(content)
        signing = ["-c", "commit.gpgsign=false"]
        if key is not None:
            signing = [
                "-c",
                "gpg.format=ssh",
                "-c",
                f"user.signingkey={key}",
                "-c",
                "commit.gpgsign=true",
            ]
        git = [*GIT, *signing, "-C", str(self.path)]
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", content], check=True)
        done = subprocess.run(
            [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        )
        return done.stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return Origin(tmp_path)


def _update(origin: Origin, dest: Path, *, verify: bool = True) -> str:
    signers = origin.allowed_signers if verify else None
    return gitrepo.update(run, str(origin.path), "main", dest, signers)


def test_a_commit_signed_by_a_trusted_key_is_checked_out(origin: Origin, tmp_path: Path) -> None:
    commit = origin.commit("v1", origin.trusted)

    assert _update(origin, tmp_path / "clone") == commit
    assert (tmp_path / "clone" / "unit.container").read_text() == "v1"


def test_an_unsigned_commit_is_refused_and_the_old_files_stay(
    origin: Origin, tmp_path: Path
) -> None:
    origin.commit("v1", origin.trusted)
    _update(origin, tmp_path / "clone")
    origin.commit("v2")

    with pytest.raises(gitrepo.UntrustedCommitError, match="no signature"):
        _update(origin, tmp_path / "clone")

    assert (tmp_path / "clone" / "unit.container").read_text() == "v1"


def test_a_commit_signed_by_another_key_is_refused(origin: Origin, tmp_path: Path) -> None:
    origin.commit("v1", origin.other)

    with pytest.raises(gitrepo.UntrustedCommitError, match="No principal matched"):
        _update(origin, tmp_path / "clone")

    assert not (tmp_path / "clone" / "unit.container").exists()


def test_a_trusted_tip_vouches_for_the_unsigned_commits_below_it(
    origin: Origin, tmp_path: Path
) -> None:
    origin.commit("v1")
    commit = origin.commit("v2", origin.trusted)

    assert _update(origin, tmp_path / "clone") == commit


def test_a_missing_signers_file_refuses_everything(origin: Origin, tmp_path: Path) -> None:
    origin.commit("v1", origin.trusted)
    origin.allowed_signers.unlink()

    with pytest.raises(gitrepo.UntrustedCommitError):
        _update(origin, tmp_path / "clone")


def test_without_verification_an_unsigned_commit_is_accepted(
    origin: Origin, tmp_path: Path
) -> None:
    commit = origin.commit("v1")

    assert _update(origin, tmp_path / "clone", verify=False) == commit
