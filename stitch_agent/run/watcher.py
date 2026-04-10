"""File watcher + lock file for `stitch run --watch`.

Polling-based watcher. We intentionally avoid adding a new dependency
(watchfiles/watchdog) for the MVP — polling every second scales fine for
typical repo sizes and is cross-platform without any native extensions.

Responsibilities:
- Walk the repo tree, skip noisy/irrelevant directories
- Snapshot (path, mtime, size) tuples
- Detect "idle after change": wait for a file change, then wait for
  debounce_seconds of quiet before returning
- Provide a cooperative lock file to prevent two stitch-watch instances
  from racing on the same repo
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Directories we never scan for changes (they're either noisy, generated, or
# irrelevant to the user's source code).
IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        ".env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".turbo",
        ".cache",
        "coverage",
        ".coverage",
        "htmlcov",
        ".idea",
        ".vscode",
        ".DS_Store",
    }
)

# Files we never report as changes.
IGNORE_FILES: frozenset[str] = frozenset(
    {
        ".stitch.lock",
        ".DS_Store",
        "Thumbs.db",
        "*.pyc",
        "*.pyo",
    }
)

# Hidden files/dirs we DO want to watch despite starting with a dot.
KEEP_HIDDEN: frozenset[str] = frozenset(
    {
        ".gitlab-ci.yml",
        ".github",
        ".gitignore",
    }
)


def _is_ignored_part(part: str) -> bool:
    if part in IGNORE_DIRS:
        return True
    if part in KEEP_HIDDEN:
        return False
    return part.startswith(".") and part not in (".", "..")


def should_ignore(path: Path, repo_root: Path) -> bool:
    """Return True if a path should be excluded from the watcher."""
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return True
    parts = rel.parts
    if not parts:
        return False
    # Check every directory segment; allow the final filename if it's in
    # KEEP_HIDDEN (e.g. .gitlab-ci.yml).
    for part in parts[:-1]:
        if _is_ignored_part(part):
            return True
    last = parts[-1]
    if last in IGNORE_FILES:
        return True
    # Allow hidden filenames that we explicitly want to watch.
    if last in KEEP_HIDDEN:
        return False
    return last.startswith(".")


def snapshot(repo_root: Path) -> dict[str, tuple[float, int]]:
    """Return a {path: (mtime, size)} snapshot of the repo tree."""
    snap: dict[str, tuple[float, int]] = {}
    stack: list[Path] = [repo_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            name = entry.name
            if entry.is_dir():
                if _is_ignored_part(name):
                    continue
                stack.append(entry)
                continue
            if not entry.is_file():
                continue
            if name in IGNORE_FILES:
                continue
            if name.startswith(".") and name not in KEEP_HIDDEN:
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            rel = entry.relative_to(repo_root).as_posix()
            snap[rel] = (st.st_mtime, st.st_size)
    return snap


@dataclass
class WatchConfig:
    debounce_seconds: float = 3.0
    poll_interval: float = 1.0


async def wait_for_change_then_idle(
    repo_root: Path,
    config: WatchConfig | None = None,
) -> None:
    """Block until a file changes, then wait for debounce_seconds of quiet.

    Phase 1: poll until any file changes.
    Phase 2: poll until no changes occur for `debounce_seconds`.
    """
    cfg = config or WatchConfig()
    baseline = await asyncio.to_thread(snapshot, repo_root)
    current = baseline

    # Phase 1 — wait for any change.
    while current == baseline:
        await asyncio.sleep(cfg.poll_interval)
        current = await asyncio.to_thread(snapshot, repo_root)

    # Phase 2 — wait for the filesystem to settle.
    last_change_ts = time.monotonic()
    while True:
        await asyncio.sleep(cfg.poll_interval)
        new_snap = await asyncio.to_thread(snapshot, repo_root)
        if new_snap != current:
            current = new_snap
            last_change_ts = time.monotonic()
            continue
        if time.monotonic() - last_change_ts >= cfg.debounce_seconds:
            return


class LockAcquireError(Exception):
    """Raised when the stitch lock cannot be acquired."""


class StitchLock:
    """Cooperative lock file. Stores the current PID in `.stitch.lock`.

    If the lock file exists and the PID is alive, raise LockAcquireError.
    If the PID is stale (process no longer exists), take over the lock.
    """

    def __init__(self, repo_root: Path) -> None:
        self.path = repo_root / ".stitch.lock"

    def acquire(self) -> None:
        if self.path.exists():
            other_pid = self._read_pid()
            if other_pid is not None and _pid_alive(other_pid):
                raise LockAcquireError(
                    f"Another stitch instance is running (pid {other_pid}). "
                    f"If this is wrong, delete {self.path} manually."
                )
            # stale lock — remove it
            with contextlib.suppress(OSError):
                self.path.unlink()
        self.path.write_text(str(os.getpid()))

    def release(self) -> None:
        try:
            current = self._read_pid()
        except Exception:
            current = None
        if current == os.getpid():
            with contextlib.suppress(OSError):
                self.path.unlink()

    def __enter__(self) -> StitchLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()

    def _read_pid(self) -> int | None:
        try:
            raw = self.path.read_text().strip()
            return int(raw)
        except (OSError, ValueError):
            return None


def _pid_alive(pid: int) -> bool:
    """Return True if the given PID is alive on this system."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but owned by another user
        return True
    except OSError:
        return False
    return True
