# Threat model

This page says what QuadDeploy protects against, what it trusts and what it leaves open. Read the last section before you rely on it.

## What is trusted

| Trusted party | Why it must be trusted |
|---|---|
| The holders of the keys in `allowed_signers`, or everyone who can push to the config branch if that is not set | They decide which units run and with which options |
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

### An unapproved change to the config repo

*Threat:* someone who gains push access to the config branch changes a unit file, or a compromised Git host serves a different history.

*Defence:* when `repo.allowed_signers` is set, the agent accepts a fetched commit only if it is signed by one of the listed SSH keys. It checks before it checks out, so the files of an unsigned commit never replace the trusted ones. This defence is optional. See the first gap below.

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

### Unit files are code, and checking them is optional

A unit file can mount the user's home directory, or run any command on the host through `ExecStartPre=`. Image signing does not cover that.

Commit verification does, but it is off unless `repo.allowed_signers` is set. Without it, anyone who can push to the config branch controls the host. `quaddeploy preflight` warns about this.

With it on, three limits remain:

- **A trusted key can do anything.** The check proves who approved a commit, not that the commit is safe. Review still matters.
- **Only the newest commit is checked.** A trusted signature on it vouches for everything below it, because a commit's hash covers its whole history.
- **Only SSH signatures count.** Commits that GitHub creates in the browser, such as pull request merges, carry GitHub's own GPG signature and are refused. Merge locally and push a commit you signed.

### An old signed image can be replayed

A signature says who built an image. It does not say the image is the newest. Someone who controls the registry can point a tag back at an older signed image, and the agent will deploy it. If that older image has a known flaw, this is a downgrade attack.

### Whoever controls the signing workflow controls the host

With keyless signing, the trusted identity is a CI workflow. Anyone who can change that workflow on the trusted branch can sign any image. Treat write access to it like production access.

### Other image references in a unit

The agent verifies the `Image=` key. A unit can name an image in other ways, such as `Mount=type=image,...`, and those are not verified or pinned.

### Credentials stored on the host

A private repo or image means the host holds a credential: a deploy key, a registry login, or both. They sit in files that the deploy user can read. Anyone who gets that far can read the repo and pull the images.

Keep them narrow. Use a deploy key with write access off, and a registry token that can only read packages. Then a stolen credential exposes the code and nothing more.

The agent hides a user name, password or token written into an HTTP URL before it logs, records or reports an error. It cannot hide one from the config file itself, so do not put a token in `repo.url`.

### What notifications reveal

A notification carries the host name, release ids, the commit hash and the error text, which can include image names and URLs. It goes to whoever runs the webhook. The webhook URL sits in the config file in plain text, and anyone who reads it can post to that endpoint.

### Secrets

QuadDeploy does not manage secrets. A secret written into a unit file in the repo is readable by everyone who can read the repo. Use `podman secret` on the host.

### Local attackers

A local user who can write to the deploy user's files can edit the config to trust a different signer, or edit the installed unit files directly. `quaddeploy status` reports edited unit files as drift, but the agent does not undo them.

### Availability

There is one host. If it is down, the service is down. If the registry or the Git host is unreachable, running services keep running, but nothing new deploys.
