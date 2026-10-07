from __future__ import annotations

from pathlib import Path

from conftest import COMMIT, FakeRunner

from quaddeploy import gitrepo

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
    assert ["git", "-C", str(tmp_path), "checkout", "--force", "--detach", "FETCH_HEAD"] in (
        fake_run.calls
    )
