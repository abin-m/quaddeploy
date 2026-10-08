<h1 align="center">QuadDeploy</h1>

<p align="center">
  Pull-based deployment for a single Linux host.<br>
  Rootless Podman, systemd Quadlets and signed container images.
</p>

<p align="center">
  <a href="https://github.com/abin-m/quaddeploy/actions/workflows/ci.yml"><img src="https://github.com/abin-m/quaddeploy/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="https://github.com/abin-m/quaddeploy/actions/workflows/integration.yml"><img src="https://github.com/abin-m/quaddeploy/actions/workflows/integration.yml/badge.svg" alt="integration"></a>
  <a href="https://github.com/abin-m/quaddeploy/actions/workflows/image.yml"><img src="https://github.com/abin-m/quaddeploy/actions/workflows/image.yml/badge.svg" alt="image"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 or later">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT licence"></a>
</p>

<p align="center">
  <img src="docs/images/overview.svg" alt="CI builds and signs an image. The host fetches the repo, verifies the signature and pulls the image by digest." width="827">
</p>

A small agent on the host pulls a Git repo on a timer and installs its [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) unit files. It runs only images that carry a valid signature, and it rolls back a release that fails its health check.

It is for the case where Kubernetes is more than the problem needs.

> **Status:** early development, built as a showcase. An end-to-end test runs in CI on Ubuntu 24.04, and the agent has been run by hand on Rocky Linux 10.2 with Podman 5.8 and cosign 3.1. It has no production use behind it.

## What it does

- **The host pulls.** CI holds no SSH key or token for the host.
- **Unsigned images never run.** Every image is checked with `cosign` before it is pulled.
- **You run what was signed.** Each image is pinned to the digest that was verified.
- **A bad release rolls back.** Config and image return together, and the failed release is not retried.

## See it run

A deploy: the image is verified, pinned to its digest and started.

<p align="center"><img src="docs/images/deploy.png" alt="quaddeploy sync deploys a release, the app answers, and the unit file names the image by digest" width="827"></p>

A release that fails its health check: it is rolled back and then refused.

<p align="center"><img src="docs/images/rollback.png" alt="quaddeploy sync rolls back an unhealthy release and rejects it on the next run" width="827"></p>

The [walkthrough](docs/walkthrough.md) shows every stage, including an unsigned image and an unattended deploy.

## How it works

`quaddeploy sync` runs these steps:

1. **Fetch** the configured branch of the config repo.
2. **Verify** every image named in the repo's Quadlet files with `cosign`. A missing or wrong signature stops the run and changes nothing.
3. **Pin** each image to the digest that was signed.
4. **Compare** the pinned files with the release that is deployed. Stop if they match.
5. **Apply** the new release: pull the images, copy the files into `~/.config/containers/systemd`, reload systemd and restart only the units that changed.
6. **Check health.** Every container unit must be active and every configured URL must answer.
7. **Roll back** to the earlier release if a unit fails to start or a check fails.

Because the agent resolves a tag to a digest on each run, pushing a new signed image to the same tag also triggers a deploy.

## Quick start

You need Linux with systemd, Podman 4.4 or later, Python 3.11 or later, Git and [cosign](https://docs.sigstore.dev/cosign/system_config/installation/).

```sh
pipx install git+https://github.com/abin-m/quaddeploy.git
```

Create `~/.config/quaddeploy/config.toml`. [examples/config.toml](examples/config.toml) is a full example.

```toml
[repo]
url = "https://github.com/alice/app.git"
branch = "main"    # default
path = "deploy"    # default

# Who must have signed the images. This form trusts one GitHub Actions workflow.
[verify]
certificate_identity = "https://github.com/alice/app/.github/workflows/image.yml@refs/heads/main"
certificate_oidc_issuer = "https://token.actions.githubusercontent.com"

[health]
timeout = 60       # seconds to wait for a healthy release
urls = ["http://127.0.0.1:8080/healthz"]
```

To verify with a public key instead, set `key = "/path/to/cosign.pub"` in `[verify]`. Image names must include their registry, such as `ghcr.io/alice/app:main`.

Then check the signatures, deploy once and turn on the timer:

```sh
quaddeploy verify
quaddeploy sync
quaddeploy init
```

## Commands

| Command | What it does |
|---|---|
| `quaddeploy verify` | Check signatures and deploy nothing |
| `quaddeploy sync` | Deploy once |
| `quaddeploy sync --force` | Retry a release that was rejected |
| `quaddeploy init` | Install a systemd timer that runs `sync` every two minutes |
| `quaddeploy status` | Show the last result and any hand-edited unit files |
| `quaddeploy rollback` | Return to the release before the current one |

`sync` exits non-zero when it fails, rolls back or skips a rejected release, so the timer's service shows as failed in `systemctl --user status quaddeploy`.

## Sign images in CI

Both examples build the demo app, push it to the platform's registry and sign it with cosign. The signature is tied to the pipeline's identity, so there is no signing key to store.

| Platform | Example | Tested |
|---|---|---|
| GitHub Actions | [.github/workflows/image.yml](.github/workflows/image.yml) | Yes, it builds this repo's demo image |
| GitLab CI | [examples/gitlab-ci.yml](examples/gitlab-ci.yml) | No, it follows GitLab's documented template but has not been run |

Each file's comments give the `[verify]` settings that trust it.

## Limits

- QuadDeploy manages one host and gives no high availability.
- It does not manage secrets. Use `podman secret`.
- It refuses `.kube` and `.build` units, because it cannot verify their images.
- It verifies the `Image=` key only. Other ways to name an image in a unit are not checked.
- Unit files are not signed. Anyone who can push to the config branch controls what runs.
- It does not overwrite unit files that it did not install.

## Read more

- [Walkthrough](docs/walkthrough.md): every stage on a real host, with output.
- [Architecture](docs/architecture.md): how the parts fit, and why.
- [Threat model](docs/threat-model.md): what it defends against and what it leaves open.
- [Comparison](docs/comparison.md): how it differs from `podman auto-update`, FetchIt, Materia, Kamal and Kubernetes.

## Develop

See [AGENTS.md](AGENTS.md) for the commands, layout and rules.
