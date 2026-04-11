"""Tests for stitch_agent.run.git."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from stitch_agent.run.git import (
    GitSnapshot,
    commit,
    push,
    snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _init_repo(tmp_path: Path) -> Path:
    """Create a git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@test.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "file.txt").write_text("hello")
    _git(["add", "file.txt"], repo)
    _git(["commit", "-m", "initial"], repo)
    return repo


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a repo + bare remote, linked via origin."""
    bare = tmp_path / "bare.git"
    bare.mkdir()
    _git(["init", "--bare", "-b", "main"], bare)

    repo = tmp_path / "repo"
    _git(["clone", str(bare), str(repo)], tmp_path)
    _git(["config", "user.email", "test@test.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "file.txt").write_text("hello")
    _git(["add", "file.txt"], repo)
    _git(["commit", "-m", "initial"], repo)
    _git(["push", "-u", "origin", "main"], repo)
    return repo, bare


# --- GitSnapshot dataclass ---


def test_snapshot_pushable() -> None:
    snap = GitSnapshot(clean=True, branch="main", has_remote=True, ahead=0)
    assert snap.pushable is True


def test_snapshot_not_pushable_dirty() -> None:
    snap = GitSnapshot(clean=False, branch="main", has_remote=True, ahead=0)
    assert snap.pushable is False


def test_snapshot_not_pushable_no_remote() -> None:
    snap = GitSnapshot(clean=True, branch="main", has_remote=False, ahead=0)
    assert snap.pushable is False


def test_snapshot_not_pushable_ahead() -> None:
    snap = GitSnapshot(clean=True, branch="main", has_remote=True, ahead=2)
    assert snap.pushable is False


# --- snapshot() ---


def test_snapshot_clean_with_remote(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    snap = snapshot(repo)
    assert snap.clean is True
    assert snap.branch == "main"
    assert snap.has_remote is True
    assert snap.ahead == 0


def test_snapshot_dirty_working_tree(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("changed")
    snap = snapshot(repo)
    assert snap.clean is False


def test_snapshot_no_remote_tracking(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    snap = snapshot(repo)
    assert snap.branch == "main"
    assert snap.has_remote is False


def test_snapshot_ahead_of_remote(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("change1")
    _git(["add", "-u"], repo)
    _git(["commit", "-m", "local1"], repo)
    (repo / "file.txt").write_text("change2")
    _git(["add", "-u"], repo)
    _git(["commit", "-m", "local2"], repo)
    snap = snapshot(repo)
    assert snap.ahead == 2


def test_snapshot_detached_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _git(["checkout", sha], repo)
    snap = snapshot(repo)
    assert snap.branch is None
    assert snap.has_remote is False


# --- commit() ---


def test_commit_modified_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "file.txt").write_text("fixed")
    cr = commit(repo, ["lint"])
    assert cr.ok is True
    assert cr.message == "fix(stitch): lint"
    assert len(cr.sha) == 40


def test_commit_no_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    cr = commit(repo, ["lint"])
    assert cr.ok is False


def test_commit_multiple_jobs(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "file.txt").write_text("fixed")
    cr = commit(repo, ["lint", "typecheck"])
    assert cr.ok is True
    assert cr.message == "fix(stitch): lint, typecheck"


# --- push() ---


def test_push_success(tmp_path: Path) -> None:
    repo, _ = _init_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("fixed")
    _git(["add", "-u"], repo)
    _git(["commit", "-m", "fix"], repo)
    pr = push(repo)
    assert pr.ok is True


def test_push_diverged(tmp_path: Path) -> None:
    repo, bare = _init_repo_with_remote(tmp_path)

    # Create a divergent commit on remote via a second clone
    clone2 = tmp_path / "clone2"
    _git(["clone", str(bare), str(clone2)], tmp_path)
    _git(["config", "user.email", "test@test.com"], clone2)
    _git(["config", "user.name", "Test"], clone2)
    (clone2 / "file.txt").write_text("remote change")
    _git(["add", "-u"], clone2)
    _git(["commit", "-m", "remote"], clone2)
    _git(["push"], clone2)

    # Local commit that diverges
    (repo / "file.txt").write_text("local change")
    _git(["add", "-u"], repo)
    _git(["commit", "-m", "local"], repo)
    pr = push(repo)
    assert pr.ok is False
    assert pr.error != ""
