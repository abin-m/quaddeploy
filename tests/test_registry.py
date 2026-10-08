from __future__ import annotations

import sys
from pathlib import Path

from quaddeploy import registry
from quaddeploy.runner import with_env


def test_credentials_env_points_both_tools_at_the_authfile(tmp_path: Path) -> None:
    authfile = tmp_path / "auth.json"
    authfile.write_text('{"auths": {}}')

    env = registry.credentials_env(authfile, tmp_path / "state")

    assert env["REGISTRY_AUTH_FILE"] == str(authfile)
    docker_config = Path(env["DOCKER_CONFIG"]) / "config.json"
    assert docker_config.is_symlink()
    assert docker_config.read_text() == '{"auths": {}}'


def test_credentials_env_follows_a_changed_setting(tmp_path: Path) -> None:
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text("first")
    second.write_text("second")
    registry.credentials_env(first, tmp_path / "state")

    env = registry.credentials_env(second, tmp_path / "state")

    assert (Path(env["DOCKER_CONFIG"]) / "config.json").read_text() == "second"


def test_a_login_made_later_is_seen_without_a_rerun(tmp_path: Path) -> None:
    authfile = tmp_path / "auth.json"
    env = registry.credentials_env(authfile, tmp_path / "state")

    authfile.write_text("logged in")

    assert (Path(env["DOCKER_CONFIG"]) / "config.json").read_text() == "logged in"


def test_commands_receive_both_variables(tmp_path: Path) -> None:
    authfile = tmp_path / "auth.json"
    runner = with_env(registry.credentials_env(authfile, tmp_path / "state"))
    code = "import os; print(os.environ['REGISTRY_AUTH_FILE']); print(os.environ['DOCKER_CONFIG'])"

    lines = runner([sys.executable, "-c", code]).stdout.splitlines()

    assert lines == [str(authfile), str(tmp_path / "state" / "registry-auth")]
