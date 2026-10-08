"""Tell a webhook when a deploy fails, rolls back or recovers."""

from __future__ import annotations

import http.client
import json
import logging
import socket
import urllib.request
from collections.abc import Mapping
from typing import Any

from quaddeploy import __version__

log = logging.getLogger(__name__)

FAILURES = frozenset({"failed", "rolled_back", "rejected"})
_TIMEOUT = 10.0
_FIELDS = ("result", "time", "commit", "release", "rejected", "error")


class NotifyError(RuntimeError):
    """The webhook could not be reached or did not accept the message."""


def event_for(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> str | None:
    """Return the event to report for this run, or None if there is no news.

    The timer runs every few minutes, so a standing failure must not report on every run.
    """
    new = current["result"]
    old = previous["result"] if previous else None
    if new not in FAILURES:
        return "recovered" if old in FAILURES else None
    if old not in FAILURES or new == "rolled_back":
        return str(new)
    # A rejected release after a rollback is the same incident. A new error is not.
    if new == "failed" and previous and current.get("error") != previous.get("error"):
        return "failed"
    return None


def message(event: str, record: Mapping[str, Any], host: str) -> str:
    """Return one line that a person can read in a chat channel."""
    error = record.get("error", "")
    if event == "rolled_back":
        before = record.get("release") or "no release"
        text = f"release {record.get('rejected')} failed and was rolled back to {before}. {error}"
    elif event == "rejected":
        text = f"the repo asks for a release that is rejected. {error}"
    elif event == "failed":
        text = f"sync failed. {error}"
    elif event == "recovered":
        text = f"recovered. Release {record.get('release')} is deployed."
    else:
        text = "this is a test notification."
    return f"QuadDeploy on {host}: {text}".strip()


def send(url: str, payload: Mapping[str, Any]) -> None:
    """POST payload to url as JSON. Raise NotifyError if it is not accepted."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": f"quaddeploy/{__version__}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT):
            pass
    except (OSError, http.client.HTTPException) as exc:
        # The URL is a secret for many webhook services, so it stays out of the message.
        raise NotifyError(str(exc)) from exc


def payload(event: str, record: Mapping[str, Any]) -> dict[str, Any]:
    """Build the message body. The text field suits chat webhooks. The rest is for scripts."""
    host = socket.gethostname()
    fields = {key: record[key] for key in _FIELDS if key in record}
    return {"text": message(event, record, host), "event": event, "host": host, **fields}


def report(
    url: str | None, previous: Mapping[str, Any] | None, current: Mapping[str, Any] | None
) -> None:
    """Send a notification if this run is news. A webhook failure never fails the run."""
    if url is None or current is None:
        return
    event = event_for(previous, current)
    if event is None:
        return
    try:
        send(url, payload(event, current))
    except NotifyError as exc:
        log.warning("could not send the %s notification: %s", event, exc)
    else:
        log.info("sent a %s notification", event)
