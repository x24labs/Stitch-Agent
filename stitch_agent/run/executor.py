"""Local job executor — run CI job scripts as subprocesses."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from stitch_agent.run.models import CIJob


@dataclass
class ExecResult:
    log: str
    exit_code: int
    timed_out: bool = False
    duration_seconds: float = 0.0


class LocalExecutor:
    """Execute CIJob script commands locally via shell subprocess."""

    def __init__(
        self, repo_root: Path, timeout_seconds: float = 300.0,
    ) -> None:
        self.repo_root = repo_root
        self.timeout_seconds = timeout_seconds

    async def run_job(self, job: CIJob) -> ExecResult:
        """Run a job's script sequentially.

        Stops on first non-zero exit. Combines stdout+stderr. Enforces an
        overall timeout across the entire job script.
        """
        if not job.script:
            return ExecResult(log="(job has no script commands)", exit_code=0)

        start = time.monotonic()
        combined_log_parts: list[str] = []
        env = dict(os.environ)
        env.setdefault("STITCH_RUN", "1")

        remaining = self.timeout_seconds
        for cmd in job.script:
            cmd_start = time.monotonic()
            combined_log_parts.append(f"$ {cmd}\n")
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(self.repo_root),
                    env=env,
                )
            except Exception as exc:  # spawn failure (shell not found etc.)
                combined_log_parts.append(f"Stitch: failed to spawn: {exc}\n")
                duration = time.monotonic() - start
                return ExecResult(
                    log="".join(combined_log_parts),
                    exit_code=127,
                    timed_out=False,
                    duration_seconds=duration,
                )

            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=max(0.1, remaining),
                )
            except TimeoutError:
                with _suppress():
                    proc.kill()
                with _suppress():
                    await proc.wait()
                combined_log_parts.append(
                    f"\nStitch: command timed out after {self.timeout_seconds}s\n"
                )
                duration = time.monotonic() - start
                return ExecResult(
                    log="".join(combined_log_parts),
                    exit_code=124,
                    timed_out=True,
                    duration_seconds=duration,
                )

            combined_log_parts.append(
                (stdout_bytes or b"").decode("utf-8", errors="replace")
            )
            exit_code = proc.returncode if proc.returncode is not None else -1
            remaining -= time.monotonic() - cmd_start
            if exit_code != 0:
                duration = time.monotonic() - start
                return ExecResult(
                    log="".join(combined_log_parts),
                    exit_code=exit_code,
                    timed_out=False,
                    duration_seconds=duration,
                )
            if remaining <= 0:
                combined_log_parts.append(
                    f"\nStitch: overall job timeout reached ({self.timeout_seconds}s)\n"
                )
                duration = time.monotonic() - start
                return ExecResult(
                    log="".join(combined_log_parts),
                    exit_code=124,
                    timed_out=True,
                    duration_seconds=duration,
                )

        duration = time.monotonic() - start
        return ExecResult(
            log="".join(combined_log_parts),
            exit_code=0,
            timed_out=False,
            duration_seconds=duration,
        )


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return True
