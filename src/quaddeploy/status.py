"""Record the result of the last run."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _path(state_dir: Path) -> Path:
    return state_dir / "status.json"


def write(state_dir: Path, result: str, **fields: Any) -> None:
    """Save the result of a run with the current time."""
    state_dir.mkdir(parents=True, exist_ok=True)
    record = {"result": result, "time": datetime.now(UTC).isoformat(timespec="seconds"), **fields}
    tmp = state_dir / ".status.json.tmp"
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(tmp, _path(state_dir))


def read(state_dir: Path) -> dict[str, Any] | None:
    """Return the last saved record, or None if the agent has never run."""
    try:
        record: dict[str, Any] = json.loads(_path(state_dir).read_text())
    except FileNotFoundError:
        return None
    return record
