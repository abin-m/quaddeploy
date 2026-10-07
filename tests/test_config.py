from __future__ import annotations

from pathlib import Path

import pytest

from quaddeploy import config

ENV = {"HOME": "/home/alice"}
REPO = '[repo]\nurl = "https://git.example.com/alice/app.git"\n'
VERIFY = '[verify]\nkey = "~/.config/quaddeploy/cosign.pub"\n'
MINIMAL = REPO + VERIFY


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_load_applies_defaults(tmp_path: Path) -> None:
    cfg = config.load(_write(tmp_path, MINIMAL), ENV)

    assert cfg.branch == "main"
    assert cfg.deploy_path == "deploy"
    assert cfg.state_dir == Path("/home/alice/.local/state/quaddeploy")
    assert cfg.unit_dir == Path("/home/alice/.config/containers/systemd")
    assert cfg.health == config.HealthConfig(timeout=60, interval=2, urls=())


def test_load_honours_xdg_variables(tmp_path: Path) -> None:
    env = {**ENV, "XDG_STATE_HOME": "/var/state", "XDG_CONFIG_HOME": "/etc/alice"}

    cfg = config.load(_write(tmp_path, MINIMAL), env)

    assert cfg.state_dir == Path("/var/state/quaddeploy")
    assert cfg.unit_dir == Path("/etc/alice/containers/systemd")


def test_load_reads_explicit_paths(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[repo]\nurl = "https://git.example.com/alice/app.git"\nbranch = "prod"\npath = "units"\n'
        '[paths]\nstate_dir = "/srv/state"\nunit_dir = "/srv/units"\n' + VERIFY,
    )

    cfg = config.load(path, ENV)

    assert (cfg.branch, cfg.deploy_path) == ("prod", "units")
    assert (cfg.state_dir, cfg.unit_dir) == (Path("/srv/state"), Path("/srv/units"))


def test_load_reads_a_verification_key(tmp_path: Path) -> None:
    text = REPO + '[verify]\nkey = "/etc/quaddeploy/cosign.pub"\n'

    cfg = config.load(_write(tmp_path, text), ENV)

    assert cfg.verify == config.VerifyConfig(key=Path("/etc/quaddeploy/cosign.pub"))


def test_load_reads_a_keyless_signer(tmp_path: Path) -> None:
    text = REPO + (
        "[verify]\n"
        'certificate_identity = "https://git.example.com/alice/app/workflow@refs/heads/main"\n'
        'certificate_oidc_issuer = "https://token.example.com"\n'
    )

    cfg = config.load(_write(tmp_path, text), ENV)

    assert cfg.verify.key is None
    assert cfg.verify.identity == "https://git.example.com/alice/app/workflow@refs/heads/main"
    assert cfg.verify.issuer == "https://token.example.com"


def test_load_reads_health_settings(tmp_path: Path) -> None:
    text = MINIMAL + '[health]\ntimeout = 30\ninterval = 0.5\nurls = ["http://127.0.0.1:8080/"]\n'

    cfg = config.load(_write(tmp_path, text), ENV)

    assert cfg.health == config.HealthConfig(30.0, 0.5, ("http://127.0.0.1:8080/",))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[repo]\n" + VERIFY,
        '[repo]\nurl = ""\n' + VERIFY,
        '[repo]\nurl = "--upload-pack=evil"\n' + VERIFY,
        REPO + 'branch = "-x"\n' + VERIFY,
        REPO + 'path = "../outside"\n' + VERIFY,
        REPO + 'path = "/etc"\n' + VERIFY,
        "not toml =",
        REPO,
        REPO + "[verify]\n",
        REPO + '[verify]\nkey = "a.pub"\ncertificate_identity = "alice@example.com"\n',
        REPO + '[verify]\ncertificate_identity = "alice@example.com"\n',
        REPO + '[verify]\ncertificate_oidc_issuer = "https://token.example.com"\n',
        REPO + '[verify]\nkey = "--key-from-option"\n',
        MINIMAL + "[health]\ntimeout = -1\n",
        MINIMAL + "[health]\ninterval = true\n",
        MINIMAL + '[health]\nurls = "http://127.0.0.1/"\n',
        MINIMAL + '[health]\nurls = ["file:///etc/passwd"]\n',
        "repo = 1\n" + VERIFY,
    ],
)
def test_load_rejects_invalid_config(tmp_path: Path, text: str) -> None:
    with pytest.raises(config.ConfigError):
        config.load(_write(tmp_path, text), ENV)


def test_load_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "absent.toml", ENV)
