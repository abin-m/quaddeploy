"""Refuse an image tag that has moved back to a digest this host already replaced."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

_FILE = "images.json"


class ReplayError(RuntimeError):
    """A tag points to an older image than the one this host deployed from it."""


@dataclass
class _Entry:
    digest: str
    replaced: list[str] = field(default_factory=list)


def _tag(ref: str) -> str | None:
    """Return ref in one spelling per tag, or None if ref names a digest and cannot move."""
    if "@" in ref:
        return None
    # A colon before the last slash belongs to the registry's port, not to a tag.
    last = ref.rpartition("/")[2]
    return ref if ":" in last else f"{ref}:latest"


def _digest(pinned: str) -> str:
    return pinned.rpartition("@")[2]


def _load(state_dir: Path) -> dict[str, _Entry]:
    path = state_dir / _FILE
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ReplayError(f"cannot read {path}: {exc}") from exc
    bad_shape = ReplayError(f"cannot read {path}: it does not hold a digest record")
    if not isinstance(data, dict):
        raise bad_shape
    entries = {}
    for tag, entry in data.items():
        if not isinstance(entry, dict):
            raise bad_shape
        digest, replaced = entry.get("digest"), entry.get("replaced")
        if not isinstance(digest, str) or not isinstance(replaced, list):
            raise bad_shape
        if not all(isinstance(old, str) for old in replaced):
            raise bad_shape
        entries[tag] = _Entry(digest, replaced)
    return entries


def check(state_dir: Path, ref: str, pinned: str) -> None:
    """Raise ReplayError if ref resolved to a digest that a later deploy of ref replaced."""
    tag = _tag(ref)
    if tag is None:
        return
    entry = _load(state_dir).get(tag)
    digest = _digest(pinned)
    if entry is not None and digest in entry.replaced:
        raise ReplayError(
            f"{ref} now points to {digest}, an image this host has already replaced with a "
            f"newer one. The registry may be serving an old image. To deploy it on purpose, "
            f"set Image={pinned} in the unit file"
        )


def record(state_dir: Path, images: Mapping[str, str]) -> None:
    """Note that each tag in images now runs its pinned digest.

    images maps each reference in the repo to its digest-pinned reference.
    """
    data = _load(state_dir)
    changed = False
    for ref, pinned in images.items():
        tag = _tag(ref)
        if tag is None:
            continue
        digest = _digest(pinned)
        entry = data.get(tag)
        if entry is None:
            data[tag] = _Entry(digest)
            changed = True
        elif entry.digest != digest:
            if entry.digest not in entry.replaced:
                entry.replaced.append(entry.digest)
            entry.digest = digest
            changed = True
    if not changed:
        return
    path = state_dir / _FILE
    tmp = path.with_name(f".{_FILE}.tmp")
    record = {tag: asdict(entry) for tag, entry in data.items()}
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
