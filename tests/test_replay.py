from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import DIGEST, OTHER_DIGEST

from quaddeploy import replay

REF = "registry.example.com/alice/web:1"
OLD = f"registry.example.com/alice/web@{DIGEST}"
NEW = f"registry.example.com/alice/web@{OTHER_DIGEST}"


def test_a_tag_moved_back_to_a_replaced_digest_is_refused(tmp_path: Path) -> None:
    replay.record(tmp_path, {REF: OLD})
    replay.record(tmp_path, {REF: NEW})

    with pytest.raises(replay.ReplayError, match="already replaced") as raised:
        replay.check(tmp_path, REF, OLD)

    assert f"Image={OLD}" in str(raised.value)


def test_the_newest_digest_and_an_unseen_one_pass(tmp_path: Path) -> None:
    replay.record(tmp_path, {REF: OLD})
    replay.record(tmp_path, {REF: NEW})

    replay.check(tmp_path, REF, NEW)
    replay.check(tmp_path, REF, f"registry.example.com/alice/web@sha256:{'c' * 64}")


def test_a_host_with_no_record_passes(tmp_path: Path) -> None:
    replay.check(tmp_path, REF, OLD)

    assert not (tmp_path / "images.json").exists()


def test_each_tag_has_its_own_record(tmp_path: Path) -> None:
    replay.record(tmp_path, {REF: OLD})
    replay.record(tmp_path, {REF: NEW})

    replay.check(tmp_path, "registry.example.com/alice/web:2", OLD)


def test_recording_the_same_digest_again_replaces_nothing(tmp_path: Path) -> None:
    replay.record(tmp_path, {REF: OLD})
    replay.record(tmp_path, {REF: OLD})

    replay.check(tmp_path, REF, OLD)
    record = json.loads((tmp_path / "images.json").read_text())
    assert record == {REF: {"digest": DIGEST, "replaced": []}}


def test_a_digest_reference_is_neither_checked_nor_recorded(tmp_path: Path) -> None:
    replay.record(tmp_path, {REF: OLD})
    replay.record(tmp_path, {REF: NEW})

    replay.record(tmp_path, {OLD: OLD})
    replay.check(tmp_path, OLD, OLD)

    assert set(json.loads((tmp_path / "images.json").read_text())) == {REF}


@pytest.mark.parametrize("registry", ["registry.example.com", "localhost:5000"])
def test_a_missing_tag_is_the_latest_tag(tmp_path: Path, registry: str) -> None:
    name = f"{registry}/alice/web"
    replay.record(tmp_path, {name: f"{name}@{DIGEST}"})
    replay.record(tmp_path, {f"{name}:latest": f"{name}@{OTHER_DIGEST}"})

    with pytest.raises(replay.ReplayError):
        replay.check(tmp_path, name, f"{name}@{DIGEST}")


def test_an_unreadable_record_stops_the_check(tmp_path: Path) -> None:
    (tmp_path / "images.json").write_text("{not json")

    with pytest.raises(replay.ReplayError, match="cannot read"):
        replay.check(tmp_path, REF, OLD)


@pytest.mark.parametrize(
    "content",
    [
        [DIGEST],
        {REF: DIGEST},
        {REF: {"digest": DIGEST}},
        {REF: {"digest": DIGEST, "replaced": [1]}},
    ],
)
def test_a_record_of_the_wrong_shape_stops_the_check(tmp_path: Path, content: object) -> None:
    (tmp_path / "images.json").write_text(json.dumps(content))

    with pytest.raises(replay.ReplayError, match="does not hold a digest record"):
        replay.check(tmp_path, REF, OLD)
