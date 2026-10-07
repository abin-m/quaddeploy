from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import DIGEST, OTHER_DIGEST, FakeRunner, cosign_output

from quaddeploy import verify
from quaddeploy.config import VerifyConfig
from quaddeploy.runner import Result

REF = "registry.example.com/alice/web:1.2"
KEY = VerifyConfig(key=Path("/etc/quaddeploy/cosign.pub"))
KEYLESS = VerifyConfig(
    identity="https://git.example.com/alice/app/.github/workflows/image.yml@refs/heads/main",
    issuer="https://token.example.com",
)


def test_verify_with_a_key_returns_the_signed_digest(fake_run: FakeRunner) -> None:
    assert verify.verify(fake_run, KEY, REF) == DIGEST
    assert fake_run.calls == [["cosign", "verify", "--key", "/etc/quaddeploy/cosign.pub", REF]]


def test_verify_keyless_passes_identity_and_issuer(fake_run: FakeRunner) -> None:
    verify.verify(fake_run, KEYLESS, REF)

    assert fake_run.calls == [
        [
            "cosign",
            "verify",
            "--certificate-identity",
            str(KEYLESS.identity),
            "--certificate-oidc-issuer",
            str(KEYLESS.issuer),
            REF,
        ]
    ]


def test_verify_raises_when_cosign_rejects_the_image(fake_run: FakeRunner) -> None:
    fake_run.results["cosign"] = Result(1, "", "no matching signatures")

    with pytest.raises(verify.VerifyError, match="no matching signatures"):
        verify.verify(fake_run, KEY, REF)


@pytest.mark.parametrize("ref", ["web:1", "alice/web:1", "--key=evil/web", ""])
def test_verify_refuses_an_image_without_a_registry(fake_run: FakeRunner, ref: str) -> None:
    with pytest.raises(verify.VerifyError, match="registry"):
        verify.verify(fake_run, KEY, ref)

    assert fake_run.calls == []


def test_verify_accepts_capitalised_keys_and_a_bare_digest(fake_run: FakeRunner) -> None:
    payload = {"Critical": {"Image": {"Docker-manifest-digest": DIGEST.removeprefix("sha256:")}}}
    fake_run.results["cosign"] = Result(0, json.dumps(payload) + "\n", "")

    assert verify.verify(fake_run, KEY, REF) == DIGEST


def test_verify_accepts_several_signatures_of_one_digest(fake_run: FakeRunner) -> None:
    payload = [{"critical": {"image": {"docker-manifest-digest": DIGEST}}}] * 2
    fake_run.results["cosign"] = Result(0, json.dumps(payload), "")

    assert verify.verify(fake_run, KEY, REF) == DIGEST


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not json",
        "[]",
        json.dumps([{"critical": {"image": {}}}]),
        json.dumps(
            [
                {"critical": {"image": {"docker-manifest-digest": DIGEST}}},
                {"critical": {"image": {"docker-manifest-digest": OTHER_DIGEST}}},
            ]
        ),
        json.dumps([{"critical": {"image": {"docker-manifest-digest": "sha256:abc\nExec=x"}}}]),
    ],
)
def test_verify_rejects_output_without_exactly_one_valid_digest(
    fake_run: FakeRunner, stdout: str
) -> None:
    fake_run.results["cosign"] = Result(0, stdout, "")

    with pytest.raises(verify.VerifyError):
        verify.verify(fake_run, KEY, REF)


@pytest.mark.parametrize(
    ("ref", "pinned"),
    [
        ("registry.example.com/alice/web:1.2", f"registry.example.com/alice/web@{DIGEST}"),
        ("registry.example.com/alice/web", f"registry.example.com/alice/web@{DIGEST}"),
        ("localhost:5000/web:latest", f"localhost:5000/web@{DIGEST}"),
        ("localhost:5000/web", f"localhost:5000/web@{DIGEST}"),
        (f"registry.example.com/web:1@{OTHER_DIGEST}", f"registry.example.com/web@{DIGEST}"),
    ],
)
def test_pin_replaces_the_tag_with_the_digest(ref: str, pinned: str) -> None:
    assert verify.pin(ref, DIGEST) == pinned


def test_cosign_output_helper_matches_the_parser(fake_run: FakeRunner) -> None:
    fake_run.results["cosign"] = cosign_output(OTHER_DIGEST)

    assert verify.verify(fake_run, KEY, REF) == OTHER_DIGEST
