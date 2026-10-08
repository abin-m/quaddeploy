from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner, Webhook

from quaddeploy import cli, notify
from quaddeploy.config import Config
from quaddeploy.runner import Result

WEB = "[Container]\nImage=registry.example.com/alice/web:1\n"
OK = {"result": "unchanged"}


def _failed(error: str = "boom") -> dict[str, Any]:
    return {"result": "failed", "error": error}


@pytest.mark.parametrize(
    ("previous", "current", "event"),
    [
        (None, {"result": "deployed"}, None),
        (OK, {"result": "deployed"}, None),
        (OK, OK, None),
        (None, _failed(), "failed"),
        (OK, _failed(), "failed"),
        (_failed(), _failed(), None),
        (_failed(), _failed("another error"), "failed"),
        (OK, {"result": "rolled_back"}, "rolled_back"),
        (_failed(), {"result": "rolled_back"}, "rolled_back"),
        ({"result": "rolled_back"}, {"result": "rejected"}, None),
        ({"result": "rejected"}, {"result": "rejected"}, None),
        (OK, {"result": "rejected"}, "rejected"),
        ({"result": "rejected"}, _failed(), "failed"),
        (_failed(), OK, "recovered"),
        ({"result": "rejected"}, {"result": "deployed"}, "recovered"),
        ({"result": "rolled_back"}, OK, "recovered"),
    ],
)
def test_event_for_reports_only_news(
    previous: dict[str, Any] | None, current: dict[str, Any], event: str | None
) -> None:
    assert notify.event_for(previous, current) == event


def test_message_for_a_rollback_names_both_releases() -> None:
    record = {"release": "aaa111", "rejected": "bbb222", "error": "healthz did not answer"}

    text = notify.message("rolled_back", record, "web1")

    assert text == (
        "QuadDeploy on web1: release bbb222 failed and was rolled back to aaa111. "
        "healthz did not answer"
    )


def test_message_for_a_failed_first_deploy_has_no_earlier_release() -> None:
    text = notify.message("rolled_back", {"release": "", "rejected": "bbb222"}, "web1")

    assert "rolled back to no release" in text


def test_send_posts_json_to_the_webhook(webhook: Webhook) -> None:
    notify.send(webhook.url, notify.payload("failed", _failed("no signatures found")))

    (body,) = webhook.received
    assert body["event"] == "failed"
    assert body["error"] == "no signatures found"
    assert body["text"].endswith("sync failed. no signatures found")
    assert body["host"]


def test_send_raises_when_the_webhook_rejects_the_message(webhook: Webhook) -> None:
    webhook.status = 500

    with pytest.raises(notify.NotifyError, match="500") as raised:
        notify.send(webhook.url, {"text": "x"})

    assert webhook.url not in str(raised.value)


def test_send_raises_when_nothing_listens() -> None:
    with pytest.raises(notify.NotifyError):
        notify.send("http://127.0.0.1:9/hook", {"text": "x"})


def test_report_does_not_raise_when_the_webhook_is_down(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notify.report("http://127.0.0.1:9/hook", OK, _failed())

    assert "could not send the failed notification" in caplog.text
    assert "127.0.0.1:9" not in caplog.text


def test_report_does_nothing_without_a_url(webhook: Webhook) -> None:
    notify.report(None, OK, _failed())

    assert webhook.received == []


@pytest.fixture
def args(tmp_path: Path, cfg: Config, webhook: Webhook) -> list[str]:
    """Command line arguments for a config that reports to the local webhook."""
    path = tmp_path / "config.toml"
    path.write_text(
        f'[repo]\nurl = "{cfg.repo_url}"\n'
        f'[paths]\nstate_dir = "{cfg.state_dir}"\nunit_dir = "{cfg.unit_dir}"\n'
        '[verify]\nkey = "/etc/quaddeploy/cosign.pub"\n'
        "[health]\ninterval = 0\n"
        f'[notify]\nurl = "{webhook.url}"\n'
    )
    return ["--config", str(path)]


def _repo_file(cfg: Config, text: str) -> None:
    (cfg.state_dir / "repo" / "deploy" / "web.container").write_text(text)


def test_sync_reports_a_rollback_once_and_then_the_recovery(
    args: list[str], cfg: Config, fake_run: FakeRunner, webhook: Webhook
) -> None:
    _repo_file(cfg, WEB)
    assert cli.main([*args, "sync"], fake_run) == 0
    assert webhook.events() == []

    _repo_file(cfg, WEB + "PublishPort=8080:8080\n")
    fake_run.results["is-active"] = [Result(3, "failed\n", ""), Result(0, "active\n", "")]
    assert cli.main([*args, "sync"], fake_run) == 1
    assert webhook.events() == ["rolled_back"]
    assert "was rolled back to" in webhook.received[0]["text"]

    # The timer's next runs find the same rejected release. They stay quiet.
    assert cli.main([*args, "sync"], fake_run) == 1
    assert cli.main([*args, "sync"], fake_run) == 1
    assert webhook.events() == ["rolled_back"]

    _repo_file(cfg, WEB + "PublishPort=9090:8080\n")
    assert cli.main([*args, "sync"], fake_run) == 0
    assert webhook.events() == ["rolled_back", "recovered"]


def test_sync_reports_a_repeated_failure_once(
    args: list[str], cfg: Config, fake_run: FakeRunner, webhook: Webhook
) -> None:
    _repo_file(cfg, WEB)
    fake_run.results["cosign"] = Result(1, "", "no signatures found")

    for _ in range(3):
        assert cli.main([*args, "sync"], fake_run) == 1

    assert webhook.events() == ["failed"]


def test_a_webhook_that_is_down_does_not_change_the_exit_code(
    args: list[str], cfg: Config, fake_run: FakeRunner, webhook: Webhook
) -> None:
    webhook.status = 503
    _repo_file(cfg, WEB)
    cli.main([*args, "sync"], fake_run)
    fake_run.results["cosign"] = Result(1, "", "no signatures found")
    _repo_file(cfg, WEB + "PublishPort=8080:8080\n")

    assert cli.main([*args, "sync"], fake_run) == 1
    assert webhook.events() == ["failed"]


def test_notify_command_sends_a_test_message(
    args: list[str], fake_run: FakeRunner, webhook: Webhook, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([*args, "notify"], fake_run) == 0

    assert webhook.events() == ["test"]
    assert "this is a test notification" in webhook.received[0]["text"]
    assert "Sent a test notification" in capsys.readouterr().out


def test_notify_command_fails_when_the_webhook_refuses(
    args: list[str], fake_run: FakeRunner, webhook: Webhook
) -> None:
    webhook.status = 404

    assert cli.main([*args, "notify"], fake_run) == 1


def test_notify_command_fails_without_a_webhook(
    tmp_path: Path, cfg: Config, fake_run: FakeRunner
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        f'[repo]\nurl = "{cfg.repo_url}"\n[verify]\nkey = "/etc/quaddeploy/cosign.pub"\n'
    )

    assert cli.main(["--config", str(path), "notify"], fake_run) == 1
