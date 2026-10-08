"""End-to-end test against real Git, Podman, systemd and cosign.

It runs only when QUADDEPLOY_E2E=1. It starts a registry on localhost:5000, signs images
with a throwaway key and installs a unit named qd-e2e.container for the current user.
Signing records an entry in the public Rekor log, so the test needs internet access.

The tests share one host and run in file order. Each builds on the state the last one left.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from quaddeploy import cli, config

pytestmark = pytest.mark.skipif(
    os.environ.get("QUADDEPLOY_E2E") != "1", reason="set QUADDEPLOY_E2E=1 to run"
)

REGISTRY = "localhost:5000"
IMAGE = f"{REGISTRY}/qd-e2e"
REGISTRY_CONTAINER = "qd-e2e-registry"
REGISTRY_IMAGE = "docker.io/library/registry:2"
UNIT = "qd-e2e.container"
SERVICE = "qd-e2e.service"
PORT = 18080
DEMO_APP = Path(__file__).parents[2] / "examples" / "demo-app"
# The developer's own Git hooks must not run on the test's commits.
GIT = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.com", "-c", "core.hooksPath="]


def sh(*argv: str, cwd: Path | None = None, check: bool = True) -> str:
    """Run a command and return its output. Show the output if it fails."""
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        pytest.fail(f"{' '.join(argv)} exited {proc.returncode}:\n{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def http_get(path: str) -> tuple[int, str]:
    """Return the status and body of a GET to the demo app, or (0, "") if it is down."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=3) as response:
            return response.status, response.read().decode().strip()
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except OSError:
        return 0, ""


class Host:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.repo = work / "repo"
        self.state = work / "state"
        self.config = work / "config.toml"
        self.config.write_text(
            f'[repo]\nurl = "{self.repo}"\nallowed_signers = "{work / "allowed_signers"}"\n'
            f'[paths]\nstate_dir = "{self.state}"\n'
            f'[verify]\nkey = "{work / "cosign.pub"}"\n'
            f'[health]\ntimeout = 30\ninterval = 1\nurls = ["http://127.0.0.1:{PORT}/healthz"]\n'
        )
        # The agent's default: the current user's real Quadlet folder.
        self.unit_dir = config.load(self.config).unit_dir

    def set_up(self) -> None:
        self.remove_unit()
        sh("podman", "rm", "-f", REGISTRY_CONTAINER, check=False)
        publish = ["-p", "127.0.0.1:5000:5000"]
        sh("podman", "run", "-d", "--rm", "--name", REGISTRY_CONTAINER, *publish, REGISTRY_IMAGE)
        self._wait_for_registry()
        subprocess.run(
            ["cosign", "generate-key-pair"],
            cwd=self.work,
            env={**os.environ, "COSIGN_PASSWORD": ""},
            check=True,
            capture_output=True,
        )
        (self.repo / "deploy").mkdir(parents=True)
        sh("git", "init", "-q", "-b", "main", str(self.repo))
        # The key that signs the config repo's commits, and the host's list of trusted keys.
        key = self.work / "commit_key"
        sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))
        public = key.with_suffix(".pub").read_text()
        (self.work / "allowed_signers").write_text(f"e2e@example.com {public}")

    def tear_down(self) -> None:
        self.remove_unit()
        sh("podman", "rm", "-f", REGISTRY_CONTAINER, check=False)

    def remove_unit(self) -> None:
        sh("systemctl", "--user", "stop", SERVICE, check=False)
        (self.unit_dir / UNIT).unlink(missing_ok=True)
        sh("systemctl", "--user", "daemon-reload", check=False)

    def _wait_for_registry(self) -> None:
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://{REGISTRY}/v2/", timeout=2):
                    return
            except OSError:
                time.sleep(1)
        pytest.fail("the local registry did not start")

    def publish(self, tag: str, version: str, *, sign: bool = True) -> None:
        """Build the demo app as IMAGE:tag and push it, with or without a signature."""
        ref = f"{IMAGE}:{tag}"
        digest_file = self.work / "digest"
        version_arg = ["--build-arg", f"APP_VERSION={version}"]
        # Podman's layer cache ignores a changed build argument that only feeds ENV.
        sh("podman", "build", "-q", "--no-cache", *version_arg, "-t", ref, str(DEMO_APP))
        sh("podman", "push", "-q", "--digestfile", str(digest_file), ref)
        if sign:
            pinned = f"{IMAGE}@{digest_file.read_text().strip()}"
            subprocess.run(
                ["cosign", "sign", "--yes", "--key", "cosign.key", pinned],
                cwd=self.work,
                env={**os.environ, "COSIGN_PASSWORD": ""},
                check=True,
                capture_output=True,
            )

    def commit_unit(self, tag: str, *, healthy: bool = True, signed: bool = True) -> None:
        """Commit a unit file to the config repo that runs IMAGE:tag."""
        lines = ["[Container]", f"Image={IMAGE}:{tag}", f"PublishPort=127.0.0.1:{PORT}:8080"]
        if not healthy:
            lines.append("Environment=HEALTHY=0")
        (self.repo / "deploy" / UNIT).write_text("\n".join(lines) + "\n")
        sh(*GIT, "-C", str(self.repo), "add", "-A")
        signing = ["-c", "commit.gpgsign=false"]
        if signed:
            key = str(self.work / "commit_key.pub")
            signing = [
                "-c",
                "gpg.format=ssh",
                "-c",
                f"user.signingkey={key}",
                "-c",
                "commit.gpgsign=true",
            ]
        message = f"run {tag}, healthy={healthy}, signed={signed}"
        sh(*GIT, *signing, "-C", str(self.repo), "commit", "-q", "--allow-empty", "-m", message)

    def quaddeploy(self, *args: str) -> int:
        return cli.main(["--config", str(self.config), *args])

    def status(self) -> dict[str, Any]:
        record: dict[str, Any] = json.loads((self.state / "status.json").read_text())
        return record

    def installed_unit(self) -> str:
        return (self.unit_dir / UNIT).read_text()


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Host]:
    work = tmp_path_factory.mktemp("e2e")
    # Podman refuses a plain-HTTP registry unless it is marked insecure.
    registries = work / "registries.conf"
    registries.write_text(f'[[registry]]\nlocation = "{REGISTRY}"\ninsecure = true\n')
    patch = pytest.MonkeyPatch()
    patch.setenv("CONTAINERS_REGISTRIES_CONF", str(registries))
    machine = Host(work)
    try:
        machine.set_up()
        yield machine
    finally:
        machine.tear_down()
        patch.undo()


def test_a_signed_image_is_deployed_pinned_to_its_digest(host: Host) -> None:
    host.publish("stable", "v1")
    host.commit_unit("stable")

    assert host.quaddeploy("sync") == 0

    assert http_get("/") == (200, "QuadDeploy demo, version v1")
    assert f"Image={IMAGE}@sha256:" in host.installed_unit()
    assert host.status()["result"] == "deployed"


def test_preflight_passes_on_this_host(host: Host, capsys: pytest.CaptureFixture[str]) -> None:
    assert host.quaddeploy("preflight") == 0

    out = capsys.readouterr().out
    assert "FAIL" not in out
    for expected in ("ok    git ", "ok    podman ", "ok    cosign ", "ok    systemd user session"):
        assert expected in out
    assert "the repo is reachable and has branch main" in out


def test_a_repeat_sync_changes_nothing(host: Host) -> None:
    started = sh("systemctl", "--user", "show", SERVICE, "-p", "ActiveEnterTimestamp")

    assert host.quaddeploy("sync") == 0

    assert host.status()["result"] == "unchanged"
    assert sh("systemctl", "--user", "show", SERVICE, "-p", "ActiveEnterTimestamp") == started


def test_a_new_image_behind_the_same_tag_is_deployed(host: Host) -> None:
    host.publish("stable", "v2")

    assert host.quaddeploy("sync") == 0

    assert http_get("/") == (200, "QuadDeploy demo, version v2")


def test_an_unsigned_image_is_refused(host: Host) -> None:
    host.publish("unsigned", "v3", sign=False)
    host.commit_unit("unsigned")
    before = host.installed_unit()

    assert host.quaddeploy("sync") == 1

    assert "signature check failed" in host.status()["error"]
    assert host.installed_unit() == before
    assert http_get("/") == (200, "QuadDeploy demo, version v2")


def test_an_unsigned_commit_is_refused(host: Host) -> None:
    # This commit asks for the release that is already running. Without the commit check,
    # sync would report "unchanged" and exit 0.
    host.commit_unit("stable", signed=False)

    assert host.quaddeploy("sync") == 1

    assert "not signed by a trusted key" in host.status()["error"]
    assert http_get("/") == (200, "QuadDeploy demo, version v2")


def test_an_unhealthy_release_is_rolled_back(host: Host) -> None:
    host.commit_unit("stable", healthy=False)
    before = host.installed_unit()

    assert host.quaddeploy("sync") == 1

    assert host.status()["result"] == "rolled_back"
    assert host.installed_unit() == before
    assert http_get("/healthz") == (200, "ok")
    assert http_get("/") == (200, "QuadDeploy demo, version v2")


def test_a_rejected_release_is_not_retried(host: Host) -> None:
    assert host.quaddeploy("sync") == 1

    assert host.status()["result"] == "rejected"
    assert http_get("/") == (200, "QuadDeploy demo, version v2")


def test_a_manual_rollback_returns_to_the_previous_release(host: Host) -> None:
    assert host.quaddeploy("rollback") == 0

    assert http_get("/") == (200, "QuadDeploy demo, version v1")
    assert sh("systemctl", "--user", "is-active", SERVICE) == "active"
