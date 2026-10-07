from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import DIGEST, FakeRunner

from quaddeploy import cli
from quaddeploy.config import Config
from quaddeploy.runner import Result

WEB = "[Container]\nImage=registry.example.com/alice/web:1\n"


@pytest.fixture
def args(tmp_path: Path, cfg: Config) -> list[str]:
    """Command line arguments that point at a config file matching cfg."""
    path = tmp_path / "config.toml"
    path.write_text(
        f'[repo]\nurl = "{cfg.repo_url}"\n'
        f'[paths]\nstate_dir = "{cfg.state_dir}"\nunit_dir = "{cfg.unit_dir}"\n'
        '[verify]\nkey = "/etc/quaddeploy/cosign.pub"\n'
        "[health]\ninterval = 0\n"
    )
    return ["--config", str(path)]


def _repo_file(cfg: Config, text: str) -> None:
    (cfg.state_dir / "repo" / "deploy" / "web.container").write_text(text)


def test_sync_then_status_succeed(
    args: list[str], cfg: Config, fake_run: FakeRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo_file(cfg, WEB)

    assert cli.main([*args, "sync"], fake_run) == 0
    assert cli.main([*args, "status"], fake_run) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["result"] == "deployed"
    assert record["drift"] == []


def test_status_reports_a_unit_file_edited_by_hand(
    args: list[str], cfg: Config, fake_run: FakeRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo_file(cfg, WEB)
    cli.main([*args, "sync"], fake_run)
    (cfg.unit_dir / "web.container").write_text("edited")

    cli.main([*args, "status"], fake_run)

    assert json.loads(capsys.readouterr().out)["drift"] == ["web.container"]


def test_failed_sync_exits_one_and_records_the_error(
    args: list[str], fake_run: FakeRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_run.results["fetch"] = Result(128, "", "could not read from remote")

    assert cli.main([*args, "sync"], fake_run) == 1
    assert cli.main([*args, "status"], fake_run) == 1
    assert "could not read from remote" in capsys.readouterr().out


def test_unsigned_image_exits_one(args: list[str], cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, WEB)
    fake_run.results["cosign"] = Result(1, "", "no matching signatures")

    assert cli.main([*args, "sync"], fake_run) == 1
    assert cli.main([*args, "verify"], fake_run) == 1


def test_rolled_back_sync_exits_one_until_forced(
    args: list[str], cfg: Config, fake_run: FakeRunner
) -> None:
    _repo_file(cfg, WEB)
    fake_run.results["restart"] = Result(1, "", "unit failed")

    assert cli.main([*args, "sync"], fake_run) == 1
    del fake_run.results["restart"]
    assert cli.main([*args, "sync"], fake_run) == 1
    assert cli.main([*args, "sync", "--force"], fake_run) == 0


def test_verify_prints_each_pinned_image(
    args: list[str], cfg: Config, fake_run: FakeRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo_file(cfg, WEB)

    assert cli.main([*args, "verify"], fake_run) == 0
    expected = f"registry.example.com/alice/web:1 -> registry.example.com/alice/web@{DIGEST}\n"
    assert capsys.readouterr().out == expected
    assert not cfg.unit_dir.exists()


def test_rollback_command(args: list[str], cfg: Config, fake_run: FakeRunner) -> None:
    _repo_file(cfg, WEB)
    cli.main([*args, "sync"], fake_run)
    assert cli.main([*args, "rollback"], fake_run) == 1

    _repo_file(cfg, WEB + "PublishPort=8080:8080\n")
    cli.main([*args, "sync"], fake_run)

    assert cli.main([*args, "rollback"], fake_run) == 0
    assert "PublishPort" not in (cfg.unit_dir / "web.container").read_text()


def test_missing_config_exits_two(tmp_path: Path, fake_run: FakeRunner) -> None:
    assert cli.main(["--config", str(tmp_path / "absent.toml"), "sync"], fake_run) == 2


def test_status_before_any_run_exits_one(args: list[str], fake_run: FakeRunner) -> None:
    assert cli.main([*args, "status"], fake_run) == 1
