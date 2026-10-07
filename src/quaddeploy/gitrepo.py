"""Keep a local clone of the config repo at the tip of one branch."""

from __future__ import annotations

from pathlib import Path

from quaddeploy.runner import Runner


def update(run: Runner, url: str, branch: str, dest: Path) -> str:
    """Check out the latest commit of branch into dest and return its hash."""
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", url, str(dest)])
    git = ["git", "-C", str(dest)]
    run([*git, "remote", "set-url", "origin", url])
    run([*git, "fetch", "--prune", "origin", branch])
    run([*git, "checkout", "--force", "--detach", "FETCH_HEAD"])
    run([*git, "clean", "-fdx"])
    return run([*git, "rev-parse", "HEAD"]).stdout.strip()
