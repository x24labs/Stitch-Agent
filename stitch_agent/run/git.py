"""Git state inspection and safe commit/push for post-fix automation."""

from __future__ import annotations

import contextlib
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class GitSnapshot:
    """Pre-run git state."""

    clean: bool
    branch: str | None
    has_remote: bool
    ahead: int

    @property
    def committable(self) -> bool:
        """True when working tree was clean and we're on a named branch."""
        return self.clean and self.branch is not None

    @property
    def pushable(self) -> bool:
        """True when committable and not ahead of remote (or no remote yet)."""
        return self.committable and not (self.has_remote and self.ahead > 0)


@dataclass
class CommitResult:
    ok: bool
    sha: str = ""
    message: str = ""


@dataclass
class PushResult:
    ok: bool
    error: str = ""


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def snapshot(repo_root: Path) -> GitSnapshot:
    """Capture current git state. Never raises; returns safe defaults on error."""
    # Clean check
    status = _run(["git", "status", "--porcelain"], repo_root)
    clean = status.returncode == 0 and status.stdout.strip() == ""

    # Branch name
    branch_result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if branch_result.returncode != 0:
        return GitSnapshot(clean=False, branch=None, has_remote=False, ahead=0)
    branch = branch_result.stdout.strip()
    if branch == "HEAD":
        # Detached HEAD
        return GitSnapshot(clean=clean, branch=None, has_remote=False, ahead=0)

    # Remote tracking branch
    upstream = _run(["git", "rev-parse", "--abbrev-ref", "@{u}"], repo_root)
    if upstream.returncode != 0:
        return GitSnapshot(clean=clean, branch=branch, has_remote=False, ahead=0)

    # Ahead count
    ahead_result = _run(["git", "rev-list", "@{u}..HEAD", "--count"], repo_root)
    ahead = 0
    if ahead_result.returncode == 0:
        with contextlib.suppress(ValueError):
            ahead = int(ahead_result.stdout.strip())

    return GitSnapshot(clean=clean, branch=branch, has_remote=True, ahead=ahead)


def commit(repo_root: Path, fixed_jobs: list[str]) -> CommitResult:
    """Stage tracked changes and commit with conventional message."""
    _run(["git", "add", "-u"], repo_root)

    # Check if anything was staged
    diff_check = _run(["git", "diff", "--cached", "--quiet"], repo_root)
    if diff_check.returncode == 0:
        # Nothing staged
        return CommitResult(ok=False, message="no changes to commit")

    message = f"fix(stitch): {', '.join(fixed_jobs)}"
    result = _run(["git", "commit", "-m", message], repo_root)
    if result.returncode != 0:
        return CommitResult(ok=False, message=result.stderr.strip())

    sha_result = _run(["git", "rev-parse", "HEAD"], repo_root)
    sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""

    return CommitResult(ok=True, sha=sha, message=message)


def push(repo_root: Path) -> PushResult:
    """Push to tracking remote. Fast-forward only, never --force.

    If no upstream is configured, sets it with ``git push -u origin <branch>``.
    """
    # Check if upstream exists
    upstream = _run(["git", "rev-parse", "--abbrev-ref", "@{u}"], repo_root)
    if upstream.returncode != 0:
        # No upstream: set it with -u
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root)
        if branch.returncode != 0:
            return PushResult(ok=False, error="cannot determine current branch")
        result = _run(
            ["git", "push", "-u", "origin", branch.stdout.strip()], repo_root,
        )
    else:
        result = _run(["git", "push"], repo_root)

    if result.returncode != 0:
        return PushResult(ok=False, error=result.stderr.strip())
    return PushResult(ok=True)
