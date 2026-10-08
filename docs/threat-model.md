# Threat model

This page says what QuadDeploy protects against, what it trusts and what it leaves open. Read the last section before you rely on it.

## What is trusted

| Trusted party | Why it must be trusted |
|---|---|
| Everyone who can push to the config repo's branch | They decide which units run and with which options |
| The signer named in `[verify]` | An image they sign will run |
| The host, its root user and the deploy user | They can change the agent, its config and its state |
| The `cosign`, `git` and `podman` binaries on the host | The agent acts on what they report |
| Sigstore's public services, for keyless signing | They vouch for the identity in the signing certificate |

The registry is **not** trusted. Neither is the network between the host and the registry or the repo.

## What QuadDeploy defends against

### A tampered or substituted image

*Threat:* someone with write access to the registry, or a position on the network, replaces an image with their own.

*Defence:* the agent verifies the signature of every image before it pulls. An image without a valid signature from the configured signer stops the run, and nothing on the host changes.

### A tag moved after verification

*Threat:* the tag points at a signed image when it is checked and at another image when it is pulled.

*Defence:* the agent pulls by the digest that cosign verified and writes that digest into the unit file. The tag is never used to fetch or run anything.

### An image signed by someone else

*Threat:* an attacker signs their image with their own key or their own CI identity.

*Defence:* verification is bound to one public key, or to one certificate identity and issuer. With the example config, only the `image.yml` workflow on the `main` branch of one repo is accepted. A signature from a fork, another branch or another workflow fails.

### A short image name that resolves differently

*Threat:* `Image=app:1` could mean one registry to cosign and another to Podman, so one image is verified and a different one runs.

*Defence:* the agent refuses image names that do not include their registry.

### Images the agent cannot see

*Threat:* a unit pulls an image that the agent never verified.

*Defence:* the agent refuses `.kube` units, whose images are named in a separate YAML file, and `.build` units, which build an image on the host. It verifies `Image=` in `.container`, `.image` and `.volume` files.

### Stolen deploy credentials

*Threat:* an attacker takes over CI and uses its credentials to reach the host.

*Defence:* there are none to take. CI holds no SSH key or token for the host. The host needs read access only.

### A bad release taking the service down

*Threat:* a release that starts and then fails leaves the service down.

*Defence:* the agent rolls back to the previous release when a unit fails or a health check does not pass, and it does not retry the failed release.

### Injection through config values

*Threat:* a value in the config or in cosign's output adds an option to a command or a directive to a unit file.

*Defence:* commands are run as argument lists with no shell. Values that reach a command line may not start with a dash. The digest written into a unit file must match the exact shape of a SHA-256 digest. The timer schedule is restricted to the characters of a calendar expression.

### Damage from a compromised container

*Threat:* a process escapes its container.

*Defence:* containers run under rootless Podman as an unprivileged user. An escape gains that user's rights, not root.

## What it does not defend against

These are real gaps. Some are choices and some are work not yet done.

### Unit files are code, and they are not signed

Image signing covers images only. Anyone who can push to the config branch can add `Volume=` lines that mount the user's home directory, or an `ExecStartPre=` line that runs any command on the host as the deploy user. They cannot run an unsigned image, but they do not need one.

The agent fetches the repo over HTTPS and does not verify commit signatures. Protect the branch: require reviews, restrict who can push, and require signed commits on the hosting side.

### An old signed image can be replayed

A signature says who built an image. It does not say the image is the newest. Someone who controls the registry can point a tag back at an older signed image, and the agent will deploy it. If that older image has a known flaw, this is a downgrade attack.

### Whoever controls the signing workflow controls the host

With keyless signing, the trusted identity is a CI workflow. Anyone who can change that workflow on the trusted branch can sign any image. Treat write access to it like production access.

### Other image references in a unit

The agent verifies the `Image=` key. A unit can name an image in other ways, such as `Mount=type=image,...`, and those are not verified or pinned.

### Secrets

QuadDeploy does not manage secrets. A secret written into a unit file in the repo is readable by everyone who can read the repo. Use `podman secret` on the host.

### Local attackers

A local user who can write to the deploy user's files can edit the config to trust a different signer, or edit the installed unit files directly. `quaddeploy status` reports edited unit files as drift, but the agent does not undo them.

### Availability

There is one host. If it is down, the service is down. If the registry or the Git host is unreachable, running services keep running, but nothing new deploys.
