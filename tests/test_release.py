from __future__ import annotations

from pathlib import Path

import pytest

from quaddeploy import release

FILES = {"web.container": b"[Container]\nImage=a\n", "data.volume": b"[Volume]\n"}


def test_release_id_depends_on_names_and_content() -> None:
    base = release.release_id(FILES)

    assert release.release_id(dict(reversed(FILES.items()))) == base
    assert release.release_id({**FILES, "web.container": b"changed"}) != base
    assert release.release_id({"web.container": FILES["web.container"]}) != base


def test_write_release_round_trips(tmp_path: Path) -> None:
    written = release.write_release(tmp_path, "abc", FILES)

    assert release.load_release(written.path) == written
    assert (written.path / "web.container").read_bytes() == FILES["web.container"]


def test_current_is_none_before_the_first_deploy(tmp_path: Path) -> None:
    assert release.current(tmp_path) is None


def test_set_current_then_current(tmp_path: Path) -> None:
    written = release.write_release(tmp_path / "releases", "abc", FILES)

    release.set_current(tmp_path, written)

    assert release.current(tmp_path) == written


def test_set_current_remembers_the_release_it_replaced(tmp_path: Path) -> None:
    first = release.write_release(tmp_path / "releases", "abc", FILES)
    second = release.write_release(tmp_path / "releases", "def", {"web.container": b"new"})
    release.set_current(tmp_path, first)
    assert release.previous(tmp_path) is None

    release.set_current(tmp_path, second)
    assert release.previous(tmp_path) == first

    release.clear_previous(tmp_path)
    assert release.previous(tmp_path) is None


def test_rejected_pointer_round_trips(tmp_path: Path) -> None:
    assert release.rejected(tmp_path) is None

    release.set_rejected(tmp_path, "abc123")
    assert release.rejected(tmp_path) == "abc123"

    release.clear_rejected(tmp_path)
    assert release.rejected(tmp_path) is None


def test_write_release_records_pinned_images(tmp_path: Path) -> None:
    images = {"registry.example.com/web:1": "registry.example.com/web@sha256:abc"}

    written = release.write_release(tmp_path, "abc", FILES, images)

    assert release.load_release(written.path).images == images


def test_remove_deletes_every_file_of_the_release(tmp_path: Path) -> None:
    units = tmp_path / "units"
    written = release.write_release(tmp_path / "releases", "abc", FILES)
    release.install(written, None, units)
    (units / "manual.container").write_bytes(b"written by hand")

    release.remove(written, units)

    assert [p.name for p in units.iterdir()] == ["manual.container"]


def test_drift_lists_edited_and_missing_files(tmp_path: Path) -> None:
    units = tmp_path / "units"
    written = release.write_release(tmp_path / "releases", "abc", FILES)
    release.install(written, None, units)
    assert release.drift(written, units) == []

    (units / "web.container").write_bytes(b"edited")
    (units / "data.volume").unlink()

    assert release.drift(written, units) == ["data.volume", "web.container"]


def test_diff_lists_changed_and_removed(tmp_path: Path) -> None:
    old = release.write_release(tmp_path, "abc", FILES)
    new = release.write_release(
        tmp_path, "def", {"web.container": b"new", "front.network": b"[Network]\n"}
    )

    changes = release.diff(new, old)

    assert changes.changed == ["front.network", "web.container"]
    assert changes.removed == ["data.volume"]


def test_install_copies_new_files_and_deletes_dropped_ones(tmp_path: Path) -> None:
    units = tmp_path / "units"
    old = release.write_release(tmp_path / "releases", "abc", FILES)
    release.install(old, None, units)
    new = release.write_release(tmp_path / "releases", "def", {"web.container": b"new"})

    release.install(new, old, units)

    assert sorted(p.name for p in units.iterdir()) == ["web.container"]
    assert (units / "web.container").read_bytes() == b"new"


def test_install_refuses_to_overwrite_a_file_it_did_not_install(tmp_path: Path) -> None:
    units = tmp_path / "units"
    units.mkdir()
    (units / "web.container").write_bytes(b"written by hand")
    new = release.write_release(tmp_path / "releases", "abc", FILES)

    with pytest.raises(release.UnmanagedFileError):
        release.install(new, None, units)

    assert (units / "web.container").read_bytes() == b"written by hand"
    assert not (units / "data.volume").exists()


def test_install_accepts_an_identical_file_left_by_a_failed_run(tmp_path: Path) -> None:
    units = tmp_path / "units"
    units.mkdir()
    (units / "web.container").write_bytes(FILES["web.container"])
    new = release.write_release(tmp_path / "releases", "abc", FILES)

    release.install(new, None, units)

    assert (units / "data.volume").exists()
