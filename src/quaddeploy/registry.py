"""Give cosign and Podman the same registry credentials."""

from __future__ import annotations

from pathlib import Path


def credentials_env(authfile: Path, state_dir: Path) -> dict[str, str]:
    """Return the environment that makes both tools read authfile.

    Podman honours REGISTRY_AUTH_FILE. cosign does too, but only when the user has no
    Docker config file at all. DOCKER_CONFIG always wins for cosign, and it names a
    folder that must hold a file called config.json, so a link to authfile is kept there.
    """
    folder = state_dir / "registry-auth"
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / "config.json"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(authfile)
    return {"REGISTRY_AUTH_FILE": str(authfile), "DOCKER_CONFIG": str(folder)}
