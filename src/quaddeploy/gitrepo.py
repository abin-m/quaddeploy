"""Keep a local clone of the config repo at the tip of one branch."""

from __future__ import annotations

import shlex
from pathlib import Path

from quaddeploy.runner import Runner


class UntrustedCommitError(RuntimeError):
    """The fetched commit is not signed by a trusted key."""


def base_command(ssh_key: Path | None = None) -> list[str]:
    """Return the git command, set up to log in with ssh_key if one is given."""
    if ssh_key is None:
        return ["git"]
    # IdentitiesOnly stops ssh from offering other keys first. BatchMode stops it asking.
    ssh = f"ssh -i {shlex.quote(str(ssh_key))} -o IdentitiesOnly=yes -o BatchMode=yes"
    return ["git", "-c", f"core.sshCommand={ssh}"]


def _verify(run: Runner, git: list[str], commit: str, allowed_signers: Path) -> None:
    """Raise unless commit carries an SSH signature from a key in allowed_signers."""
    result = run(
        [
            *git,
            "-c",
            f"gpg.ssh.allowedSignersFile={allowed_signers}",
            # Only SSH signatures count. A GPG signature would be judged against
            # whatever keys happen to be in this user's keyring.
            "-c",
            "gpg.openpgp.program=false",
            "-c",
            "gpg.x509.program=false",
            "verify-commit",
            commit,
        ],
        check=False,
    )
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        reason = lines[-1] if lines else "it has no signature"
        raise UntrustedCommitError(f"commit {commit[:12]} is not signed by a trusted key: {reason}")


def update(
    run: Runner,
    url: str,
    branch: str,
    dest: Path,
    allowed_signers: Path | None = None,
    ssh_key: Path | None = None,
) -> str:
    """Check out the latest commit of branch into dest and return its hash.

    With allowed_signers, refuse a commit that is not signed by one of those keys. The
    check runs before the checkout, so untrusted files never replace trusted ones.
    """
    base = base_command(ssh_key)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run([*base, "clone", "--no-checkout", url, str(dest)])
    git = [*base, "-C", str(dest)]
    run([*git, "remote", "set-url", "origin", url])
    run([*git, "fetch", "--prune", "origin", branch])
    commit = run([*git, "rev-parse", "FETCH_HEAD"]).stdout.strip()
    if allowed_signers is not None:
        _verify(run, git, commit, allowed_signers)
    run([*git, "checkout", "--force", "--detach", commit])
    run([*git, "clean", "-fdx"])
    return commit
