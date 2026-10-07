"""Check image signatures with cosign and pin images to the verified digest."""

from __future__ import annotations

import json
import re
from typing import Any

from quaddeploy.config import VerifyConfig
from quaddeploy.runner import Runner

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class VerifyError(RuntimeError):
    """An image has no valid signature from the configured signer."""


def _check_qualified(ref: str) -> None:
    # cosign and Podman resolve short names differently, so a short name could verify
    # one image and run another.
    host, slash, _ = ref.partition("/")
    named_host = "." in host or ":" in host or host == "localhost"
    if ref.startswith("-") or not slash or not named_host:
        raise VerifyError(f"{ref}: image must include its registry, like registry.example.com/app")


def _field(mapping: Any, key: str) -> Any:
    """Look up key without regard to case. cosign versions differ in key casing."""
    if isinstance(mapping, dict):
        for name, value in mapping.items():
            if name.lower() == key:
                return value
    return None


def _payloads(stdout: str) -> list[Any]:
    """Parse cosign's output, which is one JSON array or one JSON object per line."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    return data if isinstance(data, list) else [data]


def verify(run: Runner, cfg: VerifyConfig, ref: str) -> str:
    """Verify the signature of ref and return the digest that was signed."""
    _check_qualified(ref)
    if cfg.key is not None:
        signer = ["--key", str(cfg.key)]
    else:
        signer = [
            "--certificate-identity",
            str(cfg.identity),
            "--certificate-oidc-issuer",
            str(cfg.issuer),
        ]
    result = run(["cosign", "verify", *signer, ref], check=False)
    if result.returncode != 0:
        raise VerifyError(f"{ref}: signature check failed: {result.stderr.strip()}")

    try:
        payloads = _payloads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerifyError(f"{ref}: cannot read cosign output: {exc}") from exc
    digests = set()
    for payload in payloads:
        value = _field(_field(_field(payload, "critical"), "image"), "docker-manifest-digest")
        if isinstance(value, str):
            digests.add(value if value.startswith("sha256:") else f"sha256:{value}")
    if len(digests) != 1:
        raise VerifyError(f"{ref}: expected one signed digest, found {len(digests)}")
    digest = digests.pop()
    # The digest is written into a unit file, so it must be exactly a digest.
    if not _DIGEST.fullmatch(digest):
        raise VerifyError(f"{ref}: cosign returned an invalid digest")
    return digest


def pin(ref: str, digest: str) -> str:
    """Return ref with its tag or digest replaced by digest."""
    name = ref.split("@", 1)[0]
    head, slash, last = name.rpartition("/")
    return f"{head}{slash}{last.split(':', 1)[0]}@{digest}"
