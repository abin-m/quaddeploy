"""Bring the host's Quadlet units in line with the config repo."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from quaddeploy import gitrepo, health, quadlet, release, status, verify
from quaddeploy.config import Config, HealthConfig
from quaddeploy.release import Release
from quaddeploy.runner import CommandError, Runner

log = logging.getLogger(__name__)

_SYSTEMCTL = ["systemctl", "--user"]

HealthCheck = Callable[[Runner, Sequence[str], HealthConfig], None]


class ReconcileError(RuntimeError):
    """The repo content cannot be deployed, or a rollback failed."""


@dataclass(frozen=True)
class Desired:
    commit: str
    files: dict[str, bytes]
    images: dict[str, str]


@dataclass(frozen=True)
class Outcome:
    result: str
    commit: str
    release: str
    changed: list[str]
    removed: list[str]
    error: str = ""


def desired(config: Config, run: Runner) -> Desired:
    """Fetch the repo, verify every image and return the unit files pinned to digests."""
    commit = gitrepo.update(run, config.repo_url, config.branch, config.state_dir / "repo")
    deploy_dir = config.state_dir / "repo" / config.deploy_path
    if not deploy_dir.is_dir():
        raise ReconcileError(f"{config.deploy_path} is not a folder in the repo")
    units = quadlet.find_units(deploy_dir)
    # An empty folder would stop every unit. Treat it as a mistake in the repo.
    if not units:
        raise ReconcileError(f"no Quadlet files in {config.deploy_path}")

    files: dict[str, bytes] = {}
    images: dict[str, str] = {}
    for path in units:
        if path.suffix in quadlet.UNVERIFIABLE_SUFFIXES:
            raise ReconcileError(f"{path.name}: {path.suffix} units are not supported")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ReconcileError(f"{path.name} is not UTF-8 text") from exc
        for ref in quadlet.image_refs(text):
            if ref not in images:
                images[ref] = verify.pin(ref, verify.verify(run, config.verify, ref))
        files[path.name] = quadlet.rewrite_images(text, images).encode()
    return Desired(commit, files, images)


def _workloads(target: Release) -> list[str]:
    return [quadlet.service_name(name) for name in target.files if quadlet.is_workload(name)]


def _switch(run: Runner, unit_dir: Path, target: Release | None, source: Release | None) -> None:
    """Replace the installed files of source with those of target and start what changed."""
    if target is None:
        if source is not None:
            for name in source.files:
                run([*_SYSTEMCTL, "stop", quadlet.service_name(name)], check=False)
            release.remove(source, unit_dir)
            run([*_SYSTEMCTL, "daemon-reload"])
        return

    changes = release.diff(target, source)
    # Stop dropped units while systemd still knows their generated service.
    for name in changes.removed:
        run([*_SYSTEMCTL, "stop", quadlet.service_name(name)], check=False)
    release.install(target, source, unit_dir)
    run([*_SYSTEMCTL, "daemon-reload"])
    # Volumes and networks come first so that workloads find them.
    for name in sorted(changes.changed, key=quadlet.is_workload):
        verb = "restart" if quadlet.is_workload(name) else "start"
        run([*_SYSTEMCTL, verb, quadlet.service_name(name)])


def sync(
    config: Config,
    run: Runner,
    *,
    force: bool = False,
    wait: HealthCheck = health.wait_healthy,
) -> Outcome:
    """Deploy the repo's unit files if they differ from the current release.

    Roll back to the current release if the new one fails to start or is unhealthy.
    """
    want = desired(config, run)
    state_dir = config.state_dir
    old = release.current(state_dir)
    new_id = release.release_id(want.files)

    if old and old.id == new_id:
        log.info("release %s is up to date", old.id)
        status.write(state_dir, "unchanged", commit=want.commit, release=old.id)
        return Outcome("unchanged", want.commit, old.id, [], [])

    old_id = old.id if old else ""
    if not force and release.rejected(state_dir) == new_id:
        error = f"release {new_id} is rejected. Run 'quaddeploy sync --force' to deploy it"
        status.write(state_dir, "rejected", commit=want.commit, release=old_id, error=error)
        return Outcome("rejected", want.commit, old_id, [], [], error)

    new = release.write_release(state_dir / "releases", want.commit, want.files, want.images)
    changes = release.diff(new, old)
    log.info("deploying release %s from commit %s", new.id, want.commit)
    # Pull before touching any unit, so a registry failure changes nothing.
    for pinned in sorted(set(want.images.values())):
        run(["podman", "pull", pinned])

    try:
        _switch(run, config.unit_dir, new, old)
        wait(run, _workloads(new), config.health)
    except (CommandError, health.HealthError) as exc:
        log.error("release %s failed: %s", new.id, exc)
        release.set_rejected(state_dir, new.id)
        try:
            _switch(run, config.unit_dir, old, new)
            # A restart that returns does not prove the old release is serving again.
            if old is not None:
                wait(run, _workloads(old), config.health)
        except (
            CommandError,
            OSError,
            release.UnmanagedFileError,
            health.HealthError,
        ) as failure:
            raise ReconcileError(
                f"release {new.id} failed and so did the rollback: {failure}"
            ) from failure
        log.info("rolled back to %s", old_id or "no release")
        status.write(
            state_dir,
            "rolled_back",
            commit=want.commit,
            release=old_id,
            rejected=new.id,
            error=str(exc),
        )
        return Outcome("rolled_back", want.commit, old_id, [], [], str(exc))

    release.set_current(state_dir, new)
    release.clear_rejected(state_dir)
    status.write(
        state_dir,
        "deployed",
        commit=want.commit,
        release=new.id,
        images=new.images,
        changed=changes.changed,
        removed=changes.removed,
    )
    return Outcome("deployed", want.commit, new.id, changes.changed, changes.removed)


def rollback(config: Config, run: Runner, *, wait: HealthCheck = health.wait_healthy) -> Outcome:
    """Return to the release before the current one and stop sync from redeploying it."""
    state_dir = config.state_dir
    now = release.current(state_dir)
    before = release.previous(state_dir)
    if now is None or before is None:
        raise ReconcileError("there is no earlier release to roll back to")

    log.info("rolling back from %s to %s", now.id, before.id)
    _switch(run, config.unit_dir, before, now)
    release.set_rejected(state_dir, now.id)
    release.set_current(state_dir, before)
    # Without this, a second rollback would return to the release just rejected.
    release.clear_previous(state_dir)
    wait(run, _workloads(before), config.health)

    changes = release.diff(before, now)
    status.write(state_dir, "rolled_back", commit=before.commit, release=before.id, rejected=now.id)
    return Outcome("rolled_back", before.commit, before.id, changes.changed, changes.removed)
