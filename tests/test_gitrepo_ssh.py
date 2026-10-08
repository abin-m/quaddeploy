"""Fetch over git's real SSH transport, with a stand-in for the ssh program."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quaddeploy import gitrepo
from quaddeploy.runner import run

pytestmark = pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("git-upload-pack")),
    reason="needs git and git-upload-pack on PATH",
)

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "core.hooksPath="]

# Git passes the remote command as the last argument. Running it locally gives the real
# protocol without a network or an SSH server.
FAKE_SSH = """#!/bin/sh
printf '%s\\n' "$@" >> "$FAKE_SSH_LOG"
for last; do :; done
exec sh -c "$last"
"""


@pytest.fixture
def ssh_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a stand-in ssh first on PATH and return the file it logs its arguments to."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ssh"
    fake.write_text(FAKE_SSH)
    fake.chmod(0o755)
    log = tmp_path / "ssh.log"
    log.touch()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    return log


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    path = tmp_path / "origin"
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "unit.container").write_text("v1")
    git = [*GIT, "-c", "commit.gpgsign=false", "-C", str(path)]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "v1"], check=True)
    return path


def test_a_repo_is_fetched_over_ssh_with_the_deploy_key(
    tmp_path: Path, origin: Path, ssh_log: Path
) -> None:
    key = tmp_path / "my keys" / "deploy_key"
    url = f"ssh://git@git.example.com{origin}"

    gitrepo.update(run, url, "main", tmp_path / "clone", ssh_key=key)
    # A second run takes the fetch path instead of the clone path.
    gitrepo.update(run, url, "main", tmp_path / "clone", ssh_key=key)

    assert (tmp_path / "clone" / "unit.container").read_text() == "v1"
    args = ssh_log.read_text().splitlines()
    assert str(key) in args
    assert args[args.index(str(key)) - 1] == "-i"
    assert "IdentitiesOnly=yes" in args
    assert "BatchMode=yes" in args
    assert "git@git.example.com" in args
    assert args.count(str(key)) >= 2


def test_without_a_key_ssh_gets_no_identity_option(
    tmp_path: Path, origin: Path, ssh_log: Path
) -> None:
    gitrepo.update(run, f"ssh://git@git.example.com{origin}", "main", tmp_path / "clone")

    assert "-i" not in ssh_log.read_text().splitlines()
