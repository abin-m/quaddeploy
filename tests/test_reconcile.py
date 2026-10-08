from __future__ import annotations

import pytest
from conftest import COMMIT, DIGEST, OTHER_DIGEST, FakeRunner, cosign_output

from quaddeploy import reconcile, release, status
from quaddeploy.config import Config
from quaddeploy.runner import CommandError, Result
from quaddeploy.verify import VerifyError

WEB = "[Container]\nImage=registry.example.com/alice/web:1\n"
PINNED = f"registry.example.com/alice/web@{DIGEST}"
WEB_PINNED = f"[Container]\nImage={PINNED}\n"
WEB_V2 = WEB + "PublishPort=8080:8080\n"
FAILED = Result(3, "failed\n", "")
ACTIVE = Result(0, "active\n", "")


def _repo_file(cfg: Config, name: str, text: str) -> None:
    (cfg.state_dir / "repo" / "deploy" / name).write_text(text)


def _installed(cfg: Config, name: str = "web.container") -> str:
    return (cfg.unit_dir / name).read_text()


def test_first_sync_verifies_pins_and_starts_units(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    _repo_file(cfg, "data.volume", "[Volume]\n")

    outcome = reconcile.sync(cfg, fake_run)

    assert outcome.result == "deployed"
    assert outcome.commit == COMMIT
    assert _installed(cfg) == WEB_PINNED
    assert fake_run.ran("podman") == [["podman", "pull", PINNED]]
    assert fake_run.systemctl() == [
        ["daemon-reload"],
        ["start", "data-volume.service"],
        ["restart", "web.service"],
    ]
    current = release.current(cfg.state_dir)
    assert current is not None and current.id == outcome.release
    assert current.images == {"registry.example.com/alice/web:1": PINNED}
    record = status.read(cfg.state_dir)
    assert record is not None and record["result"] == "deployed"


def test_second_sync_with_no_change_touches_nothing(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    reconcile.sync(cfg, fake_run)
    fake_run.calls.clear()

    outcome = reconcile.sync(cfg, fake_run)

    assert outcome.result == "unchanged"
    assert fake_run.systemctl() == []
    assert fake_run.ran("podman") == []


def test_sync_restarts_only_the_changed_unit(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    _repo_file(cfg, "api.container", WEB)
    reconcile.sync(cfg, fake_run)
    fake_run.calls.clear()
    _repo_file(cfg, "api.container", WEB_V2)

    outcome = reconcile.sync(cfg, fake_run)

    assert outcome.changed == ["api.container"]
    assert fake_run.systemctl() == [["daemon-reload"], ["restart", "api.service"]]


def test_a_new_image_behind_the_same_tag_is_deployed(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    reconcile.sync(cfg, fake_run)
    fake_run.results["cosign"] = cosign_output(OTHER_DIGEST)

    outcome = reconcile.sync(cfg, fake_run)

    assert outcome.result == "deployed"
    assert OTHER_DIGEST in _installed(cfg)


def test_sync_stops_and_deletes_a_dropped_unit(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    _repo_file(cfg, "old.container", WEB)
    reconcile.sync(cfg, fake_run)
    fake_run.calls.clear()
    (cfg.state_dir / "repo" / "deploy" / "old.container").unlink()

    outcome = reconcile.sync(cfg, fake_run)

    assert outcome.removed == ["old.container"]
    assert not (cfg.unit_dir / "old.container").exists()
    assert fake_run.systemctl() == [["stop", "old.service"], ["daemon-reload"]]


def test_an_unsigned_image_changes_nothing(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    fake_run.results["cosign"] = Result(1, "", "no matching signatures")

    with pytest.raises(VerifyError):
        reconcile.sync(cfg, fake_run)

    assert not cfg.unit_dir.exists()
    assert fake_run.systemctl() == []
    assert fake_run.ran("podman") == []


def test_a_failed_pull_changes_nothing(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    fake_run.results["pull"] = Result(125, "", "registry unreachable")

    with pytest.raises(CommandError):
        reconcile.sync(cfg, fake_run)

    assert not cfg.unit_dir.exists()
    assert fake_run.systemctl() == []


@pytest.mark.parametrize("name", ["stack.kube", "web.build"])
def test_sync_refuses_units_it_cannot_verify(cfg: Config, fake_run: FakeRunner, name: str) -> None:
    _repo_file(cfg, name, "")

    with pytest.raises(reconcile.ReconcileError, match="not supported"):
        reconcile.sync(cfg, fake_run)


def test_a_failed_first_deploy_is_removed_and_rejected(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    fake_run.results["restart"] = Result(1, "", "unit failed")

    outcome = reconcile.sync(cfg, fake_run)

    assert outcome.result == "rolled_back"
    assert "unit failed" in outcome.error
    assert not (cfg.unit_dir / "web.container").exists()
    assert release.current(cfg.state_dir) is None
    assert fake_run.systemctl()[-2:] == [["stop", "web.service"], ["daemon-reload"]]


def test_a_rejected_release_is_not_retried_without_force(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    fake_run.results["restart"] = Result(1, "", "unit failed")
    reconcile.sync(cfg, fake_run)
    del fake_run.results["restart"]
    fake_run.calls.clear()

    skipped = reconcile.sync(cfg, fake_run)

    assert skipped.result == "rejected"
    assert "--force" in skipped.error
    assert fake_run.systemctl() == []

    forced = reconcile.sync(cfg, fake_run, force=True)

    assert forced.result == "deployed"
    assert release.rejected(cfg.state_dir) is None


def test_an_unhealthy_release_rolls_back_to_the_previous_one(
    cfg: Config, fake_run: FakeRunner
) -> None:
    _repo_file(cfg, "web.container", WEB)
    first = reconcile.sync(cfg, fake_run)
    fake_run.calls.clear()
    _repo_file(cfg, "web.container", WEB_V2)
    fake_run.results["is-active"] = [FAILED, ACTIVE]

    outcome = reconcile.sync(cfg, fake_run)

    assert outcome.result == "rolled_back"
    assert outcome.release == first.release
    assert _installed(cfg) == WEB_PINNED
    assert fake_run.systemctl() == [
        ["daemon-reload"],
        ["restart", "web.service"],
        ["daemon-reload"],
        ["restart", "web.service"],
    ]
    current = release.current(cfg.state_dir)
    assert current is not None and current.id == first.release
    record = status.read(cfg.state_dir)
    assert record is not None and record["result"] == "rolled_back"
    assert record["rejected"] == release.rejected(cfg.state_dir)


def test_rollback_restores_a_unit_the_bad_release_dropped(
    cfg: Config, fake_run: FakeRunner
) -> None:
    _repo_file(cfg, "web.container", WEB)
    _repo_file(cfg, "api.container", WEB)
    reconcile.sync(cfg, fake_run)
    (cfg.state_dir / "repo" / "deploy" / "api.container").unlink()
    _repo_file(cfg, "web.container", WEB_V2)
    fake_run.results["is-active"] = [FAILED, ACTIVE]

    reconcile.sync(cfg, fake_run)

    assert _installed(cfg, "api.container") == WEB_PINNED
    assert fake_run.systemctl()[-2:] == [["restart", "api.service"], ["restart", "web.service"]]


def test_a_failed_rollback_is_reported(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    reconcile.sync(cfg, fake_run)
    _repo_file(cfg, "web.container", WEB_V2)
    fake_run.results["restart"] = Result(1, "", "unit failed")

    with pytest.raises(reconcile.ReconcileError, match="so did the rollback"):
        reconcile.sync(cfg, fake_run)


def test_manual_rollback_returns_to_the_previous_release(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    first = reconcile.sync(cfg, fake_run)
    _repo_file(cfg, "web.container", WEB_V2)
    second = reconcile.sync(cfg, fake_run)

    outcome = reconcile.rollback(cfg, fake_run)

    assert outcome.release == first.release
    assert _installed(cfg) == WEB_PINNED
    assert release.rejected(cfg.state_dir) == second.release
    assert reconcile.sync(cfg, fake_run).result == "rejected"
    with pytest.raises(reconcile.ReconcileError, match="no earlier release"):
        reconcile.rollback(cfg, fake_run)


def test_manual_rollback_needs_an_earlier_release(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    reconcile.sync(cfg, fake_run)

    with pytest.raises(reconcile.ReconcileError, match="no earlier release"):
        reconcile.rollback(cfg, fake_run)


def test_sync_refuses_an_empty_deploy_folder(cfg: Config, fake_run: FakeRunner) -> None:
    with pytest.raises(reconcile.ReconcileError, match="no Quadlet files"):
        reconcile.sync(cfg, fake_run)


def test_sync_refuses_a_missing_deploy_folder(cfg: Config, fake_run: FakeRunner) -> None:
    (cfg.state_dir / "repo" / "deploy").rmdir()

    with pytest.raises(reconcile.ReconcileError, match="not a folder"):
        reconcile.sync(cfg, fake_run)


def test_a_rollback_to_an_unhealthy_release_is_reported(cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, "web.container", WEB)
    reconcile.sync(cfg, fake_run)
    _repo_file(cfg, "web.container", WEB_V2)
    fake_run.results["is-active"] = FAILED

    with pytest.raises(reconcile.ReconcileError, match="so did the rollback"):
        reconcile.sync(cfg, fake_run)

    assert _installed(cfg) == WEB_PINNED
