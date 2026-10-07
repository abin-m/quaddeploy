"""Store each deployed set of unit files as a release and install it."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_MANIFEST = "release.json"


class UnmanagedFileError(RuntimeError):
    """A unit file already exists that the agent did not install."""


@dataclass(frozen=True)
class Release:
    id: str
    commit: str
    files: dict[str, str]
    path: Path
    # Image reference in the repo, mapped to the digest-pinned reference that was verified.
    images: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Changes:
    changed: list[str]
    removed: list[str]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_pointer(state_dir: Path, name: str) -> str | None:
    try:
        return (state_dir / name).read_text().strip() or None
    except FileNotFoundError:
        return None


def release_id(files: Mapping[str, bytes]) -> str:
    """Return an id that changes when any file name or content changes."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(f"{name}\0{_sha256(files[name])}\n".encode())
    return digest.hexdigest()[:12]


def write_release(
    releases_dir: Path,
    commit: str,
    files: Mapping[str, bytes],
    images: Mapping[str, str] | None = None,
) -> Release:
    """Save files as a release folder and return the release."""
    rid = release_id(files)
    path = releases_dir / rid
    path.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        _write_atomic(path / name, data)
    hashes = {name: _sha256(data) for name, data in sorted(files.items())}
    pinned = dict(images or {})
    manifest = {"id": rid, "commit": commit, "files": hashes, "images": pinned}
    _write_atomic(path / _MANIFEST, json.dumps(manifest, indent=2).encode())
    return Release(rid, commit, hashes, path, pinned)


def load_release(path: Path) -> Release:
    manifest = json.loads((path / _MANIFEST).read_text())
    return Release(
        manifest["id"],
        manifest["commit"],
        dict(manifest["files"]),
        path,
        dict(manifest.get("images", {})),
    )


def _pointed(state_dir: Path, name: str) -> Release | None:
    rid = _read_pointer(state_dir, name)
    return load_release(state_dir / "releases" / rid) if rid else None


def current(state_dir: Path) -> Release | None:
    """Return the release that is deployed now, or None before the first deploy."""
    return _pointed(state_dir, "current")


def previous(state_dir: Path) -> Release | None:
    """Return the release that was deployed before the current one, if any."""
    return _pointed(state_dir, "previous")


def set_current(state_dir: Path, release: Release) -> None:
    """Mark release as deployed and remember the one it replaced."""
    replaced = _read_pointer(state_dir, "current")
    if replaced and replaced != release.id:
        _write_atomic(state_dir / "previous", replaced.encode())
    _write_atomic(state_dir / "current", release.id.encode())


def clear_previous(state_dir: Path) -> None:
    (state_dir / "previous").unlink(missing_ok=True)


def rejected(state_dir: Path) -> str | None:
    """Return the id of the release that last failed its deploy, if any."""
    return _read_pointer(state_dir, "rejected")


def set_rejected(state_dir: Path, rid: str) -> None:
    _write_atomic(state_dir / "rejected", rid.encode())


def clear_rejected(state_dir: Path) -> None:
    (state_dir / "rejected").unlink(missing_ok=True)


def diff(new: Release, old: Release | None) -> Changes:
    """Return the file names that new adds or modifies, and those it drops."""
    old_files = old.files if old else {}
    changed = sorted(n for n, h in new.files.items() if old_files.get(n) != h)
    removed = sorted(n for n in old_files if n not in new.files)
    return Changes(changed, removed)


def install(new: Release, old: Release | None, unit_dir: Path) -> None:
    """Copy the files of new into unit_dir and delete the ones only old had."""
    old_files = old.files if old else {}
    for name, digest in new.files.items():
        target = unit_dir / name
        # A matching file is allowed so that a run that failed half way can be retried.
        unmanaged = target.exists() and name not in old_files
        if unmanaged and _sha256(target.read_bytes()) != digest:
            raise UnmanagedFileError(f"{target} exists and was not installed by quaddeploy")

    changes = diff(new, old)
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name in changes.changed:
        _write_atomic(unit_dir / name, (new.path / name).read_bytes())
    for name in changes.removed:
        (unit_dir / name).unlink(missing_ok=True)


def remove(release: Release, unit_dir: Path) -> None:
    """Delete every file of release from unit_dir."""
    for name in release.files:
        (unit_dir / name).unlink(missing_ok=True)


def drift(release: Release, unit_dir: Path) -> list[str]:
    """Return the files of release that are missing from unit_dir or were edited there."""
    drifted = []
    for name, digest in release.files.items():
        target = unit_dir / name
        if not target.is_file() or _sha256(target.read_bytes()) != digest:
            drifted.append(name)
    return sorted(drifted)
