from __future__ import annotations

import pytest
from conftest import FakeRunner

from quaddeploy import health
from quaddeploy.config import HealthConfig
from quaddeploy.runner import Result

CFG = HealthConfig(timeout=10, interval=1, urls=("http://127.0.0.1:8080/healthz",))
SERVICES = ["web.service"]


class Clock:
    """A clock that moves only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _wait(fake_run: FakeRunner, clock: Clock, url_ok: bool = True) -> None:
    health.wait_healthy(
        fake_run, SERVICES, CFG, fetch=lambda url: url_ok, sleep=clock.sleep, clock=clock
    )


def test_two_passing_checks_in_a_row_are_healthy(fake_run: FakeRunner) -> None:
    clock = Clock()

    _wait(fake_run, clock)

    assert len(fake_run.calls) == 2
    assert clock.now == 1


def test_a_failed_service_stops_the_wait_at_once(fake_run: FakeRunner) -> None:
    fake_run.results["is-active"] = Result(3, "failed\n", "")
    clock = Clock()

    with pytest.raises(health.HealthError, match="web.service failed"):
        _wait(fake_run, clock)

    assert clock.now == 0


def test_a_service_that_never_starts_times_out(fake_run: FakeRunner) -> None:
    fake_run.results["is-active"] = Result(3, "activating\n", "")
    clock = Clock()

    with pytest.raises(health.HealthError, match="web.service is activating"):
        _wait(fake_run, clock)

    assert clock.now == CFG.timeout


def test_a_slow_start_is_healthy_once_it_is_active(fake_run: FakeRunner) -> None:
    fake_run.results["is-active"] = [Result(3, "activating\n", ""), Result(0, "active\n", "")]
    clock = Clock()

    _wait(fake_run, clock)

    assert clock.now == 2


def test_one_pass_followed_by_a_crash_is_not_healthy(fake_run: FakeRunner) -> None:
    fake_run.results["is-active"] = [Result(0, "active\n", ""), Result(3, "failed\n", "")]

    with pytest.raises(health.HealthError, match="failed"):
        _wait(fake_run, Clock())


def test_a_url_that_does_not_answer_times_out(fake_run: FakeRunner) -> None:
    with pytest.raises(health.HealthError, match="127.0.0.1:8080"):
        _wait(fake_run, Clock(), url_ok=False)


def test_http_ok_is_false_when_nothing_listens() -> None:
    # Port 9 on loopback is the discard service, which is not running on a normal host.
    assert health.http_ok("http://127.0.0.1:9/") is False
