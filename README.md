# QuadDeploy

QuadDeploy deploys containers to a single Linux host from a Git repo. A small agent on the host pulls the repo on a timer and installs its [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) unit files for rootless Podman. It runs only images that carry a valid signature, and it rolls back a release that fails its health check.

It is for the case where Kubernetes is more than the problem needs.

> **Status:** early development. It has been run by hand on one host: Rocky Linux 10.2 on arm64, with Podman 5.8 and cosign 3.1. There is no automated end-to-end test yet. Do not use it for anything you care about.

## How it works

`quaddeploy sync` runs these steps:

1. **Fetch** the configured branch of the config repo.
2. **Verify** every image named in the repo's Quadlet files with `cosign`. A missing or wrong signature stops the run and changes nothing.
3. **Pin** each image to the digest that was signed, so the host runs exactly what was verified.
4. **Compare** the pinned files with the release that is deployed. Stop if they match.
5. **Apply** the new release: pull the images, copy the files into `~/.config/containers/systemd`, reload systemd and restart only the units that changed.
6. **Check health.** Every container unit must be active and every configured URL must answer.
7. **Roll back** to the earlier release if a unit fails to start or a check fails. The failed release is marked as rejected and is not tried again.

The host pulls. Nothing outside the host needs SSH access or deploy credentials.

Because the agent resolves a tag to a digest on each run, pushing a new signed image to the same tag also triggers a deploy.

## Requirements

- Linux with systemd and Podman 4.4 or later
- Python 3.11 or later
- Git and [cosign](https://docs.sigstore.dev/cosign/system_config/installation/)

## Install

```sh
pipx install git+https://github.com/abin-m/quaddeploy.git
```

## Configure

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

To verify with a public key instead, set `key = "/path/to/cosign.pub"` in `[verify]`.

Image names must include their registry, such as `ghcr.io/alice/app:main`.

## Use

```sh
quaddeploy verify          # check signatures, deploy nothing
quaddeploy sync            # deploy once
quaddeploy init            # install a systemd timer that runs sync every two minutes
quaddeploy status          # show the last result and any hand-edited unit files
quaddeploy rollback        # return to the release before the current one
quaddeploy sync --force    # retry a release that was rejected
```

`sync` exits non-zero when it fails, rolls back or skips a rejected release, so the timer's service shows as failed in `systemctl --user status quaddeploy`.

## Sign images in CI

[.github/workflows/image.yml](.github/workflows/image.yml) builds the demo app, pushes it to GitHub Container Registry and signs it with cosign. The signature is tied to the workflow's identity, so there is no signing key to store.

## Limits

- QuadDeploy manages one host and gives no high availability.
- It does not manage secrets. Use `podman secret`.
- It refuses `.kube` and `.build` units, because it cannot verify their images.
- It does not overwrite unit files that it did not install.

## Develop

See [AGENTS.md](AGENTS.md) for the commands, layout and rules.
