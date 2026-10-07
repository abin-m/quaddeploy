"""Find Quadlet unit files and read the fields the agent needs."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

# Suffix of each Quadlet file type, mapped to the suffix systemd adds to the service name.
_SERVICE_SUFFIX = {
    ".container": "",
    ".kube": "",
    ".pod": "-pod",
    ".volume": "-volume",
    ".network": "-network",
    ".image": "-image",
    ".build": "-build",
}

# Units that run workloads. The agent restarts these on change and only starts the rest.
WORKLOAD_SUFFIXES = frozenset({".container", ".kube", ".pod"})

# Units whose images the agent cannot verify: .kube names them in a separate YAML file,
# and .build makes them on the host.
UNVERIFIABLE_SUFFIXES = frozenset({".kube", ".build"})

_IMAGE_SECTIONS = frozenset({"Container", "Image"})


def is_unit(name: str) -> bool:
    return Path(name).suffix in _SERVICE_SUFFIX


def is_workload(name: str) -> bool:
    return Path(name).suffix in WORKLOAD_SUFFIXES


def find_units(directory: Path) -> list[Path]:
    """Return the Quadlet files directly inside directory, sorted by name."""
    return sorted(p for p in directory.iterdir() if p.is_file() and is_unit(p.name))


def service_name(name: str) -> str:
    """Return the systemd service that Quadlet generates for a unit file name."""
    path = Path(name)
    return f"{path.stem}{_SERVICE_SUFFIX[path.suffix]}.service"


def _image_lines(lines: Sequence[str]) -> Iterator[tuple[int, str]]:
    """Yield the index and reference of each line that names a registry image."""
    section = ""
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "Image" or section not in _IMAGE_SECTIONS:
            continue
        ref = value.strip()
        # Image=name.image or name.build points at another Quadlet file, not a registry.
        if ref and not ref.endswith((".image", ".build")):
            yield index, ref


def image_refs(text: str) -> list[str]:
    """Return the registry image references in a unit file, in order."""
    return [ref for _, ref in _image_lines(text.splitlines())]


def rewrite_images(text: str, pins: Mapping[str, str]) -> str:
    """Return text with each registry image reference replaced by its entry in pins."""
    lines = text.splitlines(keepends=True)
    for index, ref in _image_lines(lines):
        ending = "\n" if lines[index].endswith("\n") else ""
        lines[index] = f"Image={pins[ref]}{ending}"
    return "".join(lines)
