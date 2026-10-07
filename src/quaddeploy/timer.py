"""Install the systemd user timer that runs sync."""

from __future__ import annotations

import re
from pathlib import Path

from quaddeploy.runner import Runner

SERVICE = "quaddeploy.service"
TIMER = "quaddeploy.timer"
DEFAULT_SCHEDULE = "*:0/2"

# The characters of a systemd calendar expression. A newline would add unit directives.
_SCHEDULE = re.compile(r"[A-Za-z0-9*:/,.~ -]+")

_SERVICE_UNIT = """\
[Unit]
Description=QuadDeploy sync

[Service]
Type=oneshot
ExecStart={command}
"""

# A calendar schedule keeps firing after a failed run. Timers relative to the last run
# depend on how that run ended.
_TIMER_UNIT = """\
[Unit]
Description=Run QuadDeploy sync on a schedule

[Timer]
OnCalendar={schedule}

[Install]
WantedBy=timers.target
"""


def check_schedule(value: str) -> str:
    """Return value if it is safe to write as an OnCalendar expression."""
    if not _SCHEDULE.fullmatch(value):
        raise ValueError(f"not a systemd calendar expression: {value!r}")
    return value


def _quote(arg: str) -> str:
    """Quote one argument for an ExecStart line."""
    if "\n" in arg or "\r" in arg:
        raise ValueError(f"argument contains a line break: {arg!r}")
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
    # systemd expands % and $ in ExecStart. Doubling them keeps them literal.
    return '"' + escaped.replace("%", "%%").replace("$", "$$") + '"'


def install(
    run: Runner, systemd_dir: Path, executable: Path, config_path: Path, schedule: str
) -> None:
    """Write the service and timer units into systemd_dir and start the timer."""
    command = " ".join(_quote(str(a)) for a in (executable, "--config", config_path, "sync"))
    systemd_dir.mkdir(parents=True, exist_ok=True)
    (systemd_dir / SERVICE).write_text(_SERVICE_UNIT.format(command=command))
    (systemd_dir / TIMER).write_text(_TIMER_UNIT.format(schedule=check_schedule(schedule)))
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", TIMER])
