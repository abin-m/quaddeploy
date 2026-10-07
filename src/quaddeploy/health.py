"""Decide whether a new release is healthy."""

from __future__ import annotations

import http.client
import time
import urllib.request
from collections.abc import Callable, Sequence

from quaddeploy.config import HealthConfig
from quaddeploy.runner import Runner

# A unit that starts and then crashes passes one check. Two in a row catches most of those.
_PASSES = 2
_HTTP_TIMEOUT = 5.0


class HealthError(RuntimeError):
    """The release did not become healthy before the timeout."""


def http_ok(url: str) -> bool:
    """Return True if a GET of url answers with a 2xx status."""
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as response:
            status: int = response.status
            return 200 <= status < 300
    except (OSError, http.client.HTTPException):
        return False


def _problem(
    run: Runner, services: Sequence[str], cfg: HealthConfig, fetch: Callable[[str], bool]
) -> str:
    """Return the first failing check as text, or an empty string if all pass."""
    for service in services:
        state = run(["systemctl", "--user", "is-active", service], check=False).stdout.strip()
        if state == "failed":
            raise HealthError(f"{service} failed")
        if state != "active":
            return f"{service} is {state or 'in an unknown state'}"
    for url in cfg.urls:
        if not fetch(url):
            return f"{url} did not answer with success"
    return ""


def wait_healthy(
    run: Runner,
    services: Sequence[str],
    cfg: HealthConfig,
    *,
    fetch: Callable[[str], bool] = http_ok,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until every service is active and every URL answers. Raise HealthError if not."""
    deadline = clock() + cfg.timeout
    passes = 0
    while True:
        problem = _problem(run, services, cfg, fetch)
        passes = 0 if problem else passes + 1
        if passes >= _PASSES:
            return
        if clock() >= deadline:
            raise HealthError(problem or "checks did not stay green before the timeout")
        sleep(cfg.interval)
