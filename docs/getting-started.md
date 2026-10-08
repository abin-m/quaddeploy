# Deploy your own app

This guide takes you from an app in a GitHub repo to that app running on a Linux host, redeployed on every push. It takes about 30 minutes.

When you finish:

- every push to `main` builds and signs a new image
- the host picks it up within two minutes
- an unsigned image is refused, and a broken release is rolled back
- optionally, a commit that you did not sign is refused too

## Before you start

You need two things.

- **An app in a GitHub repo** with a `Dockerfile` or `Containerfile`. It should listen on a port. A URL that answers 200 when the app is healthy makes the rollback much more useful.
- **A Linux host with systemd** that you can log in to over SSH as a normal user, not root. Podman must be 4.4 or later, so use Fedora, Rocky Linux 10, Debian 13 or Ubuntu 24.04.

The examples use the GitHub user `alice` and a repo named `app`. Replace both with yours.

## Part 1: in your repo

### 1. Describe the service

Create `deploy/app.container`. This is a [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) file: a systemd unit that runs one container.

```ini
[Unit]
Description=My app

[Container]
Image=ghcr.io/alice/app:main
PublishPort=127.0.0.1:8080:8080

[Install]
WantedBy=default.target
```

Three rules apply:

- **Write the image name in full**, with its registry and in lower case. QuadDeploy refuses a short name such as `app:main`.
- **Publish a host port of 1024 or higher.** An unprivileged user cannot bind a lower one.
- **Keep the `[Install]` section.** It is what starts the service at boot.

### 2. Build and sign the image

Create `.github/workflows/image.yml`:

```yaml
name: image

on:
  push:
    branches: [main]

permissions:
  contents: read
  packages: write
  id-token: write   # lets cosign sign as this workflow, with no stored key

jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: sigstore/cosign-installer@v3
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Set the image name
        run: echo "IMAGE=ghcr.io/${GITHUB_REPOSITORY,,}" >> "$GITHUB_ENV"
      - id: push
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ env.IMAGE }}:main
      - name: Sign the image by digest
        run: cosign sign --yes "${IMAGE}@${{ steps.push.outputs.digest }}"
```

This is the workflow that builds this project's demo image, with the paths changed. If your build file is named `Containerfile`, add `file: Containerfile` under `context`.

It builds for both `amd64` and `arm64`. An image built for the wrong architecture starts and then dies at once.

### 3. Push and check

Commit both files and push. On GitHub, open the **Actions** tab and wait for `image` to pass.

Then open your profile's **Packages** tab, select the `app` package and check its visibility under **Package settings**. A public package needs nothing more. This guide has only been tested with a public image. A private one needs `podman login ghcr.io` on the host.

## Part 2: on the host

Log in over SSH as the user that will run the app. A dedicated user such as `deploy` is a good choice.

### 4. Install the tools

On Rocky Linux or Fedora:

```sh
sudo dnf install -y podman git python3-pip
```

On Debian or Ubuntu:

```sh
sudo apt install -y podman git python3-pip pipx
```

Install cosign, which is not in most distro repos:

```sh
ARCH=$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')
curl -fsSL -o cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${ARCH}"
sudo install -m 0755 cosign /usr/local/bin/cosign
```

Install QuadDeploy. Use `pipx` where you have it, and `pip install --user` otherwise:

```sh
pipx install git+https://github.com/abin-m/quaddeploy.git
```

If the shell cannot find `quaddeploy` afterwards, add `~/.local/bin` to your `PATH`.

### 5. Tell it what to follow and whom to trust

Create `~/.config/quaddeploy/config.toml`:

```toml
[repo]
url = "https://github.com/alice/app.git"

[verify]
certificate_identity = "https://github.com/alice/app/.github/workflows/image.yml@refs/heads/main"
certificate_oidc_issuer = "https://token.actions.githubusercontent.com"

[health]
urls = ["http://127.0.0.1:8080/healthz"]
```

`certificate_identity` names the one workflow whose signature the host accepts. It has this shape:

```
https://github.com/OWNER/REPO/.github/workflows/FILE@refs/heads/BRANCH
```

Leave `[health]` out if your app has no health URL. QuadDeploy then checks only that the service stays running.

### 6. Optional: accept only commits you signed

Step 5 makes the host trust one workflow for images. The unit file itself is still unprotected: anyone who can push to `main` can change what runs. This step closes that.

On your own machine, sign your commits with an SSH key. Run this in the app's repo:

```sh
git config gpg.format ssh
git config user.signingkey ~/.ssh/id_ed25519.pub
git config commit.gpgsign true
```

On the host, list the keys to trust in `~/.config/quaddeploy/allowed_signers`. Each line is an email address followed by a public key:

```
alice@example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...
```

Then add one line to the `[repo]` section of the config:

```toml
allowed_signers = "~/.config/quaddeploy/allowed_signers"
```

From now on, the newest commit on `main` must be signed by a listed key. Commits made in the GitHub web interface, including pull request merges, are signed by GitHub and are refused. Merge on your machine and push.

### 7. Check the host

```sh
quaddeploy preflight
```

It checks each requirement and prints a fix for anything missing:

```
ok    git 2.52.0
ok    podman 5.8.2
ok    cosign 3.1.3
ok    systemd user session is running
warn  lingering is off, so services and the timer stop when you log out
      Run: loginctl enable-linger
ok    config /home/deploy/.config/quaddeploy/config.toml is valid
ok    commits must be signed by a key in /home/deploy/.config/quaddeploy/allowed_signers
ok    the repo is reachable and has branch main
warn  the sync timer is not installed
      After a first 'quaddeploy sync', run: quaddeploy init

This host is ready. Next: quaddeploy verify
```

A line marked `FAIL` must be fixed. A line marked `warn` can wait until step 9.

### 8. Deploy once by hand

```sh
quaddeploy verify
quaddeploy sync
```

`verify` checks the signature and prints the digest it will run. `sync` deploys it. Then check your app:

```sh
curl http://127.0.0.1:8080/
systemctl --user status app
```

### 9. Hand over to the timer

```sh
quaddeploy init
loginctl enable-linger
```

`init` installs a timer that runs `sync` every two minutes. `enable-linger` keeps your services and the timer running after you log out, and starts them at boot.

### 10. Optional: get told when a deploy fails

A rollback at night is silent unless something tells you. Add a webhook to the config:

```toml
[notify]
url = "https://hooks.example.com/your-webhook"
```

Then send a test message:

```sh
quaddeploy notify
```

The agent posts one JSON message when a deploy fails, rolls back or recovers. The [README](../README.md#notifications) lists the events and the message format.

Treat the URL as a secret. Anyone who has it can post to your channel.

## Part 3: ship a change

Change your app, commit and push. Nothing else is needed. To watch it arrive, run this on the host:

```sh
journalctl --user -u quaddeploy -f
```

The [walkthrough](walkthrough.md#8-an-unattended-deploy) shows what that looks like.

Each push to `main` builds a new image, and each new image restarts the service. Expect a second or two of downtime per deploy.

## When something goes wrong

Start with `quaddeploy status`, then `journalctl --user -u quaddeploy` for the agent and `journalctl --user -u app` for your service.

| What you see | Cause | Fix |
|---|---|---|
| `signature check failed: ... no signatures found` | The image was never signed | Check that the `image` workflow passed |
| `signature check failed`, and the message mentions identities | The image was signed by a different workflow, branch or repo | Make `certificate_identity` match the workflow that signed it |
| `is not signed by a trusted key` | The newest commit is unsigned, or signed by a key that is not in `allowed_signers` | Sign the commit with a listed key. A merge made on GitHub does not count |
| `image must include its registry` | The unit file uses a short image name | Write the full name, such as `ghcr.io/alice/app:main` |
| `Exec format error` in the service's journal | The image was built for another CPU architecture | Build for the host's architecture. The workflow above builds both |
| `did not answer with success`, then a rollback | The health URL did not return 200 in time | Check the URL and port. For a slow start, raise `timeout` in `[health]` |
| `release ... is rejected` | That release failed earlier and was rolled back | Push a fix. To retry the same release, run `quaddeploy sync --force` |
| `exists and was not installed by quaddeploy` | A unit file with that name is already on the host | Rename yours, or remove the one on the host |
| `there is no systemd user session` | You became this user with `su` or `sudo -u` | Log in as the user directly, for example over SSH |

## Remove it

```sh
systemctl --user disable --now quaddeploy.timer
systemctl --user stop app
rm ~/.config/containers/systemd/app.container
rm ~/.config/systemd/user/quaddeploy.service ~/.config/systemd/user/quaddeploy.timer
systemctl --user daemon-reload
rm -rf ~/.local/state/quaddeploy ~/.config/quaddeploy
pipx uninstall quaddeploy
```
