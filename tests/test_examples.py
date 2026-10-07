from __future__ import annotations

from pathlib import Path

from quaddeploy import config, quadlet

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_example_config_loads() -> None:
    cfg = config.load(EXAMPLES / "config.toml", {"HOME": "/home/alice"})

    assert cfg.deploy_path == "examples/deploy"
    assert cfg.verify.identity is not None
    assert cfg.health.urls == ("http://127.0.0.1:8080/healthz",)


def test_example_units_name_a_registry_image() -> None:
    units = quadlet.find_units(EXAMPLES / "deploy")

    assert [unit.name for unit in units] == ["demo.container"]
    assert quadlet.image_refs(units[0].read_text()) == ["ghcr.io/abin-m/quaddeploy-demo:main"]
