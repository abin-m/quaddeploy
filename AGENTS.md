# Agent guide

QuadDeploy is a pull-based deployment agent for one Linux host. It reads Quadlet unit files from a Git repo, verifies their images with cosign and installs them for rootless Podman. See [README.md](README.md) for what it does and how to configure it.

## Commands

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check
.venv/bin/ruff format --check
.venv/bin/mypy
.venv/bin/pytest
```

Run all four checks before you commit. `.github/workflows/ci.yml` runs the same ones.

## Layout

| Path | Purpose |
|---|---|
| `src/quaddeploy/cli.py` | Argument parsing and exit codes |
| `src/quaddeploy/config.py` | Load and validate `config.toml` |
| `src/quaddeploy/reconcile.py` | The `sync` and `rollback` steps, in order |
| `src/quaddeploy/runner.py` | The only place that starts a subprocess |
| `src/quaddeploy/gitrepo.py` | Clone and fetch the config repo, and verify the commit's signature |
| `src/quaddeploy/quadlet.py` | Find unit files, map them to service names, read and rewrite `Image=` |
| `src/quaddeploy/verify.py` | Run `cosign verify` and pin an image to its digest |
| `src/quaddeploy/health.py` | Wait for units and URLs to be healthy |
| `src/quaddeploy/release.py` | Release folders, the state pointers, installing files |
| `src/quaddeploy/status.py` | The status file written after each run |
| `src/quaddeploy/timer.py` | The systemd timer that `init` installs |
| `src/quaddeploy/notify.py` | Decide whether a run is news, and post it to a webhook |
| `src/quaddeploy/preflight.py` | The host checks behind `preflight`, each with a fix |
| `examples/` | Demo app, demo unit file, a sample config and a GitLab CI example |
| `tests/` | Unit tests |
| `tests/integration/` | End-to-end test against real Git, Podman, systemd and cosign |
| `docs/` | Getting-started guide, walkthrough, architecture, threat model and comparison |

The state folder holds `releases/<id>/` and three pointer files: `current`, `previous` and `rejected`.

## Rules

- The runtime uses the Python standard library only. Do not add runtime dependencies.
- Call external commands through the `Runner` passed into a function. Do not import `subprocess` outside `runner.py`. Tests rely on this to run without Git, Podman, cosign or systemd.
- Pass commands as argument lists. Do not build shell strings.
- Give a command extra environment through `runner.with_env`, not `os.environ`.
- Pass any text that may hold a URL through `runner.redact` before it is logged or shown.
- Validate any value that is written into a unit file or passed to a command. A line break in a unit file adds directives, and a leading dash turns an argument into an option.
- No image runs without a verified signature. Do not add a way to skip verification.
- Write files with a temporary file and `os.replace`, as `release.py` does. A crash must not leave a half-written unit file.
- Do not overwrite or delete a unit file that QuadDeploy did not install. `release.install` enforces this.
- Target Python 3.11. Code must pass `mypy --strict`.
- Use placeholders such as `alice` and `git.example.com` in tests, examples and docs.

## Tests

Tests use the `fake_run` and `cfg` fixtures from `tests/conftest.py`. `fake_run` records each command and returns canned results. Add a test for each failure path you add, not only the success path.

The agent's real behaviour depends on systemd, Podman and cosign, which the unit tests do not exercise. `tests/integration` covers that. It is skipped unless `QUADDEPLOY_E2E=1` is set, and it needs a Linux host with a systemd user session, rootless Podman, cosign and internet access:

```sh
QUADDEPLOY_E2E=1 .venv/bin/pytest tests/integration -v
```

It starts a registry on `localhost:5000` and installs a unit named `qd-e2e.container` for the current user, then removes both. `.github/workflows/integration.yml` runs it on every push.

Say so when you report a change that only the unit tests cover.

## Writing

Use short sentences in active voice, with the point first. A comment says why, not what. Commit subjects are imperative and under 72 characters.
