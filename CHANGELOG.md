# Changelog

This project follows [semantic versioning](https://semver.org). Until 1.0, a minor version may change behaviour.

## 0.1.0

Not yet released.

The first version. It deploys Quadlet unit files from a Git repo to one host.

### Commands

- `sync` fetches the repo, verifies and pins the images, applies the units, checks health and rolls back on failure.
- `verify` checks image signatures without deploying.
- `preflight` checks that the host is ready and says how to fix what is missing.
- `init` installs a systemd user timer that runs `sync`.
- `status` shows the last result and any unit files edited by hand.
- `rollback` returns to the release before the current one.

### Behaviour

- Every image must carry a valid cosign signature, from a public key or from a keyless identity.
- Each image is pinned to the digest that was verified, and pulled by that digest.
- With `repo.allowed_signers` set, the config repo's newest commit must carry an SSH signature from a listed key.
- A release that fails to start or fails its health check is rolled back and not retried.
- Unit files that the agent did not install are never overwritten.

### Tested on

- Rocky Linux 10.2 on arm64, with Podman 5.8 and cosign 3.1.
- Ubuntu 24.04 on amd64, with Podman 4.9 and cosign 2.5, in CI.
