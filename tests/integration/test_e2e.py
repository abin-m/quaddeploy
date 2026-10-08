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
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from conftest import Webhook

from quaddeploy import cli, config, registry

pytestmark = pytest.mark.skipif(
    os.environ.get("QUADDEPLOY_E2E") != "1", reason="set QUADDEPLOY_E2E=1 to run"
)

REGISTRY = "localhost:5000"
IMAGE = f"{REGISTRY}/qd-e2e"
REGISTRY_CONTAINER = "qd-e2e-registry"
REGISTRY_IMAGE = "docker.io/library/registry:2"
# A second registry that asks for a password, to test private images.
PRIVATE_REGISTRY = "localhost:5001"
PRIVATE_IMAGE = f"{PRIVATE_REGISTRY}/qd-e2e-private"
PRIVATE_CONTAINER = "qd-e2e-private-registry"
UNIT = "qd-e2e.container"
SERVICE = "qd-e2e.service"
PORT = 18080
DEMO_APP = Path(__file__).parents[2] / "examples" / "demo-app"
# The developer's own Git hooks must not run on the test's commits.
GIT = ["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.com", "-c", "core.hooksPath="]


def sh(
    *argv: str, cwd: Path | None = None, check: bool = True, env: dict[str, str] | None = None
) -> str:
    """Run a command and return its output. Show the output if it fails."""
    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(argv, cwd=cwd, env=full_env, capture_output=True, text=True, check=False)
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
        self.webhook = Webhook()
        self.repo = work / "repo"
        self.state = work / "state"
        self.config = work / "config.toml"
        self.config.write_text(
            f'[repo]\nurl = "{self.repo}"\nallowed_signers = "{work / "allowed_signers"}"\n'
            f'[paths]\nstate_dir = "{self.state}"\n'
            f'[verify]\nkey = "{work / "cosign.pub"}"\n'
            f'[health]\ntimeout = 30\ninterval = 1\nurls = ["http://127.0.0.1:{PORT}/healthz"]\n'
            f'[notify]\nurl = "{self.webhook.url}"\n'
        )
        # The agent's default: the current user's real Quadlet folder.
        self.unit_dir = config.load(self.config).unit_dir

    def set_up(self) -> None:
        self.webhook.start()
        self.remove_unit()
        sh("podman", "rm", "-f", REGISTRY_CONTAINER, check=False)
        publish = ["-p", "127.0.0.1:5000:5000"]
        sh("podman", "run", "-d", "--rm", "--name", REGISTRY_CONTAINER, *publish, REGISTRY_IMAGE)
        self._wait_for_registry()
        sh("cosign", "generate-key-pair", cwd=self.work, env={"COSIGN_PASSWORD": ""})
        (self.repo / "deploy").mkdir(parents=True)
        sh("git", "init", "-q", "-b", "main", str(self.repo))
        # The key that signs the config repo's commits, and the host's list of trusted keys.
        key = self.work / "commit_key"
        sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))
        public = key.with_suffix(".pub").read_text()
        (self.work / "allowed_signers").write_text(f"e2e@example.com {public}")

    def tear_down(self) -> None:
        self.webhook.stop()
        self.remove_unit()
        sh("podman", "rm", "-f", "-t", "0", PRIVATE_CONTAINER, check=False)
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
            self.sign(pinned)

    def sign(self, pinned: str, **env: str) -> None:
        """Sign an image digest with the throwaway key."""
        sh(
            "cosign",
            "sign",
            "--yes",
            "--key",
            "cosign.key",
            pinned,
            cwd=self.work,
            env={"COSIGN_PASSWORD": "", **env},
        )

    def commit_unit(
        self, tag: str, *, healthy: bool = True, signed: bool = True, image: str = IMAGE
    ) -> None:
        """Commit a unit file to the config repo that runs image:tag."""
        lines = ["[Container]", f"Image={image}:{tag}", f"PublishPort=127.0.0.1:{PORT}:8080"]
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

    def start_private_registry(self) -> Path:
        """Start a registry that needs a password. Return a credentials file for it."""
        password_file = self.work / "htpasswd"
        hashed = sh(
            "podman",
            "run",
            "--rm",
            "--entrypoint",
            "htpasswd",
            "docker.io/library/httpd:2",
            "-Bbn",
            "e2e",
            "s3cret",
        )
        password_file.write_text(hashed + "\n")
        sh("podman", "rm", "-f", "-t", "0", PRIVATE_CONTAINER, check=False)
        sh(
            "podman",
            "run",
            "-d",
            "--rm",
            "--name",
            PRIVATE_CONTAINER,
            "-p",
            "127.0.0.1:5001:5000",
            "-v",
            f"{password_file}:/auth/htpasswd:ro,Z",
            "-e",
            "REGISTRY_AUTH=htpasswd",
            "-e",
            "REGISTRY_AUTH_HTPASSWD_REALM=e2e",
            "-e",
            "REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd",
            REGISTRY_IMAGE,
        )
        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://{PRIVATE_REGISTRY}/v2/", timeout=2)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            pytest.fail("the private registry did not start")
        # Logging in to a named file keeps the password out of every default location,
        # so only the agent's own setting can supply it.
        authfile = self.work / "auth.json"
        sh(
            "podman",
            "login",
            "--authfile",
            str(authfile),
            "-u",
            "e2e",
            "-p",
            "s3cret",
            PRIVATE_REGISTRY,
        )
        return authfile

    def publish_private(self, version: str, authfile: Path) -> None:
        """Build, push and sign PRIVATE_IMAGE:stable using the credentials in authfile."""
        ref = f"{PRIVATE_IMAGE}:stable"
        digest_file = self.work / "private-digest"
        version_arg = ["--build-arg", f"APP_VERSION={version}"]
        sh("podman", "build", "-q", "--no-cache", *version_arg, "-t", ref, str(DEMO_APP))
        sh(
            "podman",
            "push",
            "-q",
            "--authfile",
            str(authfile),
            "--digestfile",
            str(digest_file),
            ref,
        )
        pinned = f"{PRIVATE_IMAGE}@{digest_file.read_text().strip()}"
        self.sign(pinned, **registry.credentials_env(authfile, self.work / "signing"))

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
    registries.write_text(
        "".join(
            f'[[registry]]\nlocation = "{name}"\ninsecure = true\n'
            for name in (REGISTRY, PRIVATE_REGISTRY)
        )
    )
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
    assert host.webhook.events() == ["failed"]


def test_an_unsigned_commit_is_refused(host: Host) -> None:
    # This commit asks for the release that is already running. Without the commit check,
    # sync would report "unchanged" and exit 0.
    host.commit_unit("stable", signed=False)

    assert host.quaddeploy("sync") == 1

    assert "not signed by a trusted key" in host.status()["error"]
    assert http_get("/") == (200, "QuadDeploy demo, version v2")
    # A different error is news, so it is reported even though the last run failed too.
    assert host.webhook.events() == ["failed", "failed"]
    assert "not signed by a trusted key" in host.webhook.received[-1]["text"]


def test_an_unhealthy_release_is_rolled_back(host: Host) -> None:
    host.commit_unit("stable", healthy=False)
    before = host.installed_unit()

    assert host.quaddeploy("sync") == 1

    assert host.status()["result"] == "rolled_back"
    assert host.installed_unit() == before
    assert http_get("/healthz") == (200, "ok")
    assert http_get("/") == (200, "QuadDeploy demo, version v2")
    assert host.webhook.events() == ["failed", "failed", "rolled_back"]


def test_a_rejected_release_is_not_retried(host: Host) -> None:
    assert host.quaddeploy("sync") == 1

    assert host.status()["result"] == "rejected"
    assert http_get("/") == (200, "QuadDeploy demo, version v2")
    # The rejection belongs to the rollback that was already reported.
    assert len(host.webhook.received) == 3


def test_a_manual_rollback_returns_to_the_previous_release(host: Host) -> None:
    assert host.quaddeploy("rollback") == 0

    assert http_get("/") == (200, "QuadDeploy demo, version v1")
    assert sh("systemctl", "--user", "is-active", SERVICE) == "active"


@contextmanager
def unrelated_docker_config() -> Iterator[None]:
    """Make sure a Docker config with no login for the test registry exists.

    Its presence makes cosign ignore REGISTRY_AUTH_FILE. GitHub's runners have one. Adding
    it here keeps the test meaningful on hosts that do not.
    """
    path = Path.home() / ".docker" / "config.json"
    if path.exists():
        yield
        return
    made_folder = not path.parent.exists()
    path.parent.mkdir(exist_ok=True)
    path.write_text('{"auths": {}}')
    try:
        yield
    finally:
        path.unlink()
        if made_folder:
            path.parent.rmdir()


def test_a_private_image_deploys_once_the_credentials_file_is_set(host: Host) -> None:
    authfile = host.start_private_registry()
    host.publish_private("private", authfile)
    host.commit_unit("stable", image=PRIVATE_IMAGE)

    with unrelated_docker_config():
        # Without credentials, cosign cannot read the signature and nothing is deployed.
        assert host.quaddeploy("sync") == 1
        assert "signature check failed" in host.status()["error"]
        assert http_get("/") == (200, "QuadDeploy demo, version v1")

        host.config.write_text(host.config.read_text() + f'[registry]\nauthfile = "{authfile}"\n')

        assert host.quaddeploy("sync") == 0
    assert http_get("/") == (200, "QuadDeploy demo, version private")
    assert f"Image={PRIVATE_IMAGE}@sha256:" in host.installed_unit()
    assert host.webhook.events()[-2:] == ["failed", "recovered"]
