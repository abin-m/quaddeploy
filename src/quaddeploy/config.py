"""Load the agent's config file."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class ConfigError(ValueError):
    """The config file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class VerifyConfig:
    """Who must have signed an image: a public key, or a keyless identity and issuer."""

    key: Path | None = None
    identity: str | None = None
    issuer: str | None = None


@dataclass(frozen=True)
class HealthConfig:
    timeout: float = 60.0
    interval: float = 2.0
    urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    repo_url: str
    branch: str
    deploy_path: str
    state_dir: Path
    unit_dir: Path
    verify: VerifyConfig
    health: HealthConfig = HealthConfig()
    # SSH keys allowed to sign commits of the config repo. None turns the check off.
    allowed_signers: Path | None = None
    # Webhook that is told when a deploy fails, rolls back or recovers. None turns it off.
    notify_url: str | None = None
    # Private key that git logs in with, for a private config repo over SSH.
    ssh_key: Path | None = None
    # Registry credentials file, given to both cosign and Podman.
    registry_authfile: Path | None = None


def _xdg(env: Mapping[str, str], var: str, fallback: str) -> Path:
    value = env.get(var)
    return Path(value) if value else Path(env.get("HOME", "~")).expanduser() / fallback


def default_config_path(env: Mapping[str, str] = os.environ) -> Path:
    return _xdg(env, "XDG_CONFIG_HOME", ".config") / "quaddeploy" / "config.toml"


def systemd_user_dir(env: Mapping[str, str] = os.environ) -> Path:
    return _xdg(env, "XDG_CONFIG_HOME", ".config") / "systemd" / "user"


def _table(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table")
    return value


def _string(table: Mapping[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    # git and cosign would read a leading dash as an option.
    if value.startswith("-"):
        raise ConfigError(f"{where}.{key} must not start with '-'")
    return value


def _seconds(table: Mapping[str, Any], key: str, default: float) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ConfigError(f"health.{key} must be a number of seconds, zero or more")
    return float(value)


def _verify(table: Mapping[str, Any]) -> VerifyConfig:
    keyless = "certificate_identity" in table or "certificate_oidc_issuer" in table
    if ("key" in table) == keyless:
        raise ConfigError(
            "[verify] needs either key, or certificate_identity and certificate_oidc_issuer"
        )
    if not keyless:
        return VerifyConfig(key=Path(_string(table, "key", "verify")).expanduser())
    return VerifyConfig(
        identity=_string(table, "certificate_identity", "verify"),
        issuer=_string(table, "certificate_oidc_issuer", "verify"),
    )


def _path(table: Mapping[str, Any], key: str, where: str) -> Path | None:
    """Return an optional path setting, or None if it is not set."""
    if key not in table:
        return None
    return Path(_string(table, key, where)).expanduser()


def _notify(table: Mapping[str, Any]) -> str | None:
    if "url" not in table:
        return None
    url = _string(table, "url", "notify")
    if not url.startswith(("http://", "https://")):
        raise ConfigError("notify.url must start with http:// or https://")
    return url


def _health(table: Mapping[str, Any]) -> HealthConfig:
    urls = table.get("urls", [])
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        raise ConfigError("health.urls must be a list of strings")
    for url in urls:
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"health.urls: {url!r} must start with http:// or https://")
    defaults = HealthConfig()
    return HealthConfig(
        timeout=_seconds(table, "timeout", defaults.timeout),
        interval=_seconds(table, "interval", defaults.interval),
        urls=tuple(urls),
    )


def load(path: Path, env: Mapping[str, str] = os.environ) -> Config:
    """Read and validate the config file at path."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    repo = _table(data, "repo")
    paths = _table(data, "paths")

    deploy_path = _string(repo, "path", "repo", default="deploy")
    parts = PurePosixPath(deploy_path)
    if parts.is_absolute() or ".." in parts.parts:
        raise ConfigError("repo.path must be a relative path inside the repo")

    signers = repo.get("allowed_signers")
    allowed_signers = (
        None if signers is None else Path(_string(repo, "allowed_signers", "repo")).expanduser()
    )

    state_default = _xdg(env, "XDG_STATE_HOME", ".local/state") / "quaddeploy"
    unit_default = _xdg(env, "XDG_CONFIG_HOME", ".config") / "containers" / "systemd"
    return Config(
        repo_url=_string(repo, "url", "repo"),
        branch=_string(repo, "branch", "repo", default="main"),
        deploy_path=deploy_path,
        state_dir=Path(_string(paths, "state_dir", "paths", str(state_default))).expanduser(),
        unit_dir=Path(_string(paths, "unit_dir", "paths", str(unit_default))).expanduser(),
        verify=_verify(_table(data, "verify")),
        health=_health(_table(data, "health")),
        allowed_signers=allowed_signers,
        notify_url=_notify(_table(data, "notify")),
        ssh_key=_path(repo, "ssh_key", "repo"),
        registry_authfile=_path(_table(data, "registry"), "authfile", "registry"),
    )
