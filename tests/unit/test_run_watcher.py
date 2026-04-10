"""Tests for stitch_agent.run.watcher."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

from stitch_agent.run.watcher import (
    LockAcquireError,
    StitchLock,
    WatchConfig,
    should_ignore,
    snapshot,
    wait_for_change_then_idle,
)

if TYPE_CHECKING:
    from pathlib import Path


# ------------------------- should_ignore --------------------------------- #


def test_ignore_git_directory(tmp_path: Path) -> None:
    p = tmp_path / ".git" / "config"
    p.parent.mkdir()
    p.write_text("")
    assert should_ignore(p, tmp_path) is True


def test_ignore_node_modules(tmp_path: Path) -> None:
    p = tmp_path / "node_modules" / "foo" / "index.js"
    p.parent.mkdir(parents=True)
    p.write_text("")
    assert should_ignore(p, tmp_path) is True


def test_ignore_pycache(tmp_path: Path) -> None:
    p = tmp_path / "src" / "__pycache__" / "foo.pyc"
    p.parent.mkdir(parents=True)
    p.write_text("")
    assert should_ignore(p, tmp_path) is True


def test_do_not_ignore_normal_source_file(tmp_path: Path) -> None:
    p = tmp_path / "src" / "main.py"
    p.parent.mkdir()
    p.write_text("")
    assert should_ignore(p, tmp_path) is False


def test_do_not_ignore_gitlab_ci(tmp_path: Path) -> None:
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text("")
    assert should_ignore(p, tmp_path) is False


def test_do_not_ignore_github_workflows(tmp_path: Path) -> None:
    p = tmp_path / ".github" / "workflows" / "ci.yml"
    p.parent.mkdir(parents=True)
    p.write_text("")
    assert should_ignore(p, tmp_path) is False


def test_ignore_random_hidden_file(tmp_path: Path) -> None:
    p = tmp_path / ".envrc"
    p.write_text("")
    assert should_ignore(p, tmp_path) is True


def test_ignore_stitch_lock(tmp_path: Path) -> None:
    p = tmp_path / ".stitch.lock"
    p.write_text("123")
    assert should_ignore(p, tmp_path) is True


# ------------------------- snapshot --------------------------------------- #


def test_snapshot_contains_files_not_ignored(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / ".gitlab-ci.yml").write_text("lint:\n  script: ruff\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("")

    snap = snapshot(tmp_path)
    assert "a.py" in snap
    assert ".gitlab-ci.yml" in snap
    assert "node_modules/ignored.js" not in snap
    assert ".git/config" not in snap


def test_snapshot_detects_content_changes(tmp_path: Path) -> None:
    f = tmp_path / "file.py"
    f.write_text("one")
    snap1 = snapshot(tmp_path)

    # Wait a bit to ensure mtime changes, then rewrite
    import time as _time

    _time.sleep(0.02)
    f.write_text("two, slightly longer")

    snap2 = snapshot(tmp_path)
    assert snap1 != snap2
    assert snap2["file.py"][1] != snap1["file.py"][1]  # different size


def test_snapshot_detects_new_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    snap1 = snapshot(tmp_path)
    (tmp_path / "b.py").write_text("y")
    snap2 = snapshot(tmp_path)
    assert "b.py" in snap2
    assert "b.py" not in snap1


# ------------------------- wait_for_change_then_idle ---------------------- #


@pytest.mark.asyncio
async def test_wait_for_change_then_idle_fires_on_change(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    cfg = WatchConfig(debounce_seconds=0.3, poll_interval=0.05)

    async def mutate() -> None:
        await asyncio.sleep(0.1)
        (tmp_path / "a.py").write_text("y, bigger content")

    await asyncio.gather(
        wait_for_change_then_idle(tmp_path, cfg),
        mutate(),
    )
    # If we reach here, the function returned as expected after idle.


@pytest.mark.asyncio
async def test_wait_for_change_respects_debounce(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    cfg = WatchConfig(debounce_seconds=0.4, poll_interval=0.05)

    mutation_count = 0

    async def mutate() -> None:
        nonlocal mutation_count
        # Two mutations separated by 0.15s (both within debounce window)
        await asyncio.sleep(0.1)
        (tmp_path / "a.py").write_text("content one")
        mutation_count += 1
        await asyncio.sleep(0.15)
        (tmp_path / "a.py").write_text("content two but longer")
        mutation_count += 1

    import time as _time

    start = _time.monotonic()
    await asyncio.gather(
        wait_for_change_then_idle(tmp_path, cfg),
        mutate(),
    )
    elapsed = _time.monotonic() - start
    # Must have waited at least until after the second mutation + debounce
    assert mutation_count == 2
    assert elapsed >= 0.1 + 0.15 + 0.3  # first + gap + most of debounce


@pytest.mark.asyncio
async def test_wait_for_change_ignores_ignored_files(tmp_path: Path) -> None:
    """Writes to .git/... must not trigger the watcher."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "a.py").write_text("x")
    cfg = WatchConfig(debounce_seconds=0.2, poll_interval=0.05)

    async def mutate_ignored_then_real() -> None:
        # Write to ignored path — watcher must NOT fire on this
        await asyncio.sleep(0.1)
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
        await asyncio.sleep(0.2)
        # Now write to a real file — this SHOULD trigger
        (tmp_path / "a.py").write_text("y, slightly longer")

    import time as _time

    start = _time.monotonic()
    await asyncio.gather(
        wait_for_change_then_idle(tmp_path, cfg),
        mutate_ignored_then_real(),
    )
    elapsed = _time.monotonic() - start
    # Must have waited past the real mutation (0.3s) + debounce (0.2s)
    assert elapsed >= 0.4


# ------------------------- StitchLock ------------------------------------- #


def test_lock_acquire_and_release(tmp_path: Path) -> None:
    lock = StitchLock(tmp_path)
    lock.acquire()
    assert (tmp_path / ".stitch.lock").exists()
    assert int((tmp_path / ".stitch.lock").read_text()) == os.getpid()
    lock.release()
    assert not (tmp_path / ".stitch.lock").exists()


def test_lock_blocks_second_acquire(tmp_path: Path) -> None:
    lock1 = StitchLock(tmp_path)
    lock1.acquire()
    try:
        lock2 = StitchLock(tmp_path)
        with pytest.raises(LockAcquireError):
            lock2.acquire()
    finally:
        lock1.release()


def test_lock_clears_stale_lock_from_dead_pid(tmp_path: Path) -> None:
    # Write a stale lock with a PID that is almost certainly not running
    (tmp_path / ".stitch.lock").write_text("999999999")
    lock = StitchLock(tmp_path)
    lock.acquire()  # should silently take over
    assert int((tmp_path / ".stitch.lock").read_text()) == os.getpid()
    lock.release()


def test_lock_context_manager(tmp_path: Path) -> None:
    with StitchLock(tmp_path) as lock:
        assert lock.path.exists()
    assert not (tmp_path / ".stitch.lock").exists()


def test_lock_release_does_not_delete_others_lock(tmp_path: Path) -> None:
    """If we never acquired, release must not delete someone else's lock."""
    (tmp_path / ".stitch.lock").write_text("999999999")
    lock = StitchLock(tmp_path)
    lock.release()  # should be a no-op — we don't own this
    assert (tmp_path / ".stitch.lock").exists()
