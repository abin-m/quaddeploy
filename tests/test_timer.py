from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeRunner

from quaddeploy import cli, timer
from quaddeploy.runner import Result

EXE = Path("/home/alice/.local/bin/quaddeploy")
CONFIG = Path("/home/alice/.config/quaddeploy/config.toml")


def test_install_writes_both_units_and_starts_the_timer(
    tmp_path: Path, fake_run: FakeRunner
) -> None:
    timer.install(fake_run, tmp_path / "user", EXE, CONFIG, "*:0/5")

    service = (tmp_path / "user" / "quaddeploy.service").read_text()
    assert f'ExecStart="{EXE}" "--config" "{CONFIG}" "sync"\n' in service
    assert "Type=oneshot" in service
    assert "OnCalendar=*:0/5\n" in (tmp_path / "user" / "quaddeploy.timer").read_text()
    assert fake_run.systemctl() == [["daemon-reload"], ["enable", "--now", "quaddeploy.timer"]]


def test_install_escapes_characters_that_systemd_expands(
    tmp_path: Path, fake_run: FakeRunner
) -> None:
    timer.install(fake_run, tmp_path, Path('/opt/my "apps"/100%/$bin'), CONFIG, "hourly")

    service = (tmp_path / "quaddeploy.service").read_text()
    assert r'ExecStart="/opt/my \"apps\"/100%%/$$bin" ' in service


@pytest.mark.parametrize("schedule", ["*:0/2", "hourly", "Mon..Fri 09:00", "*-*-* 04:00:00"])
def test_check_schedule_accepts_calendar_expressions(schedule: str) -> None:
    assert timer.check_schedule(schedule) == schedule


@pytest.mark.parametrize("schedule", ["", "hourly\nExecStart=/bin/true", "daily; rm", "a=b"])
def test_check_schedule_rejects_other_text(schedule: str) -> None:
    with pytest.raises(ValueError, match="calendar"):
        timer.check_schedule(schedule)


def test_install_refuses_a_path_with_a_line_break(tmp_path: Path, fake_run: FakeRunner) -> None:
    with pytest.raises(ValueError, match="line break"):
        timer.install(fake_run, tmp_path, Path("/opt/a\nb"), CONFIG, "hourly")

    assert fake_run.calls == []


def _config_file(tmp_path: Path) -> str:
    path = tmp_path / "config.toml"
    path.write_text(
        '[repo]\nurl = "https://git.example.com/alice/app.git"\n'
        '[verify]\nkey = "/etc/quaddeploy/cosign.pub"\n'
    )
    return str(path)


def test_init_command_installs_under_the_user_config_folder(
    tmp_path: Path,
    fake_run: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_file = _config_file(tmp_path)

    assert cli.main(["--config", config_file, "init", "--schedule", "hourly"], fake_run) == 0

    service = (tmp_path / "xdg" / "systemd" / "user" / "quaddeploy.service").read_text()
    assert f'"--config" "{Path(config_file).resolve()}" "sync"' in service
    assert "loginctl enable-linger" in capsys.readouterr().out


def test_init_command_reports_a_systemctl_failure(
    tmp_path: Path, fake_run: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    fake_run.results["enable"] = Result(1, "", "Failed to connect to bus")

    assert cli.main(["--config", _config_file(tmp_path), "init"], fake_run) == 1
