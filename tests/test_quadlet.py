from __future__ import annotations

from pathlib import Path

import pytest

from quaddeploy import quadlet

UNIT = """\
[Unit]
Description=Web app
# Image=registry.example.com/commented:1

[Container]
Image = registry.example.com/alice/web:1.2
PublishPort=8080:8080

[Install]
WantedBy=default.target
"""


def test_find_units_returns_only_quadlet_files(tmp_path: Path) -> None:
    for name in ("web.container", "data.volume", "README.md", "notes.txt"):
        (tmp_path / name).write_text("")
    (tmp_path / "nested.container").mkdir()

    assert [p.name for p in quadlet.find_units(tmp_path)] == ["data.volume", "web.container"]


@pytest.mark.parametrize(
    ("name", "service"),
    [
        ("web.container", "web.service"),
        ("stack.kube", "stack.service"),
        ("app.pod", "app-pod.service"),
        ("data.volume", "data-volume.service"),
        ("front.network", "front-network.service"),
        ("base.image", "base-image.service"),
        ("web.build", "web-build.service"),
    ],
)
def test_service_name(name: str, service: str) -> None:
    assert quadlet.service_name(name) == service


def test_image_refs_reads_the_container_section() -> None:
    assert quadlet.image_refs(UNIT) == ["registry.example.com/alice/web:1.2"]


def test_image_refs_skips_references_to_other_units() -> None:
    text = "[Container]\nImage=base.image\n"

    assert quadlet.image_refs(text) == []


def test_rewrite_images_changes_only_the_image_line() -> None:
    pinned = "registry.example.com/alice/web@sha256:" + "a" * 64

    rewritten = quadlet.rewrite_images(UNIT, {"registry.example.com/alice/web:1.2": pinned})

    assert rewritten == UNIT.replace(
        "Image = registry.example.com/alice/web:1.2", f"Image={pinned}"
    )
    assert "# Image=registry.example.com/commented:1" in rewritten


def test_rewrite_images_keeps_a_file_without_a_final_newline() -> None:
    text = "[Container]\nImage=registry.example.com/web:1"

    assert quadlet.rewrite_images(text, {"registry.example.com/web:1": "x"}) == (
        "[Container]\nImage=x"
    )


def test_rewrite_images_leaves_references_to_other_units() -> None:
    text = "[Container]\nImage=base.image\n"

    assert quadlet.rewrite_images(text, {}) == text


def test_image_refs_ignores_image_keys_in_other_sections() -> None:
    text = "[Service]\nImage=registry.example.com/alice/web:1\n"

    assert quadlet.image_refs(text) == []
