"""Local job executor — run CI job scripts as subprocesses."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
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


_PKG_MGR_RE = re.compile(
    r"^\s*(apt-get|apt|yum|dnf|apk)\s",
)

_PIP_RE = re.compile(r"(^|\s|&&|\|\||;)\s*pip3?\s+(install|uninstall)\b")


def _needs_sudo(cmd: str) -> bool:
    """Return True if the command is a system package manager call and we're not root."""
    if os.geteuid() == 0:
        return False
    return bool(_PKG_MGR_RE.match(cmd))


def _script_uses_pip(script: list[str]) -> bool:
    """Return True if any command in the script invokes pip install."""
    return any(_PIP_RE.search(cmd) for cmd in script)


def _in_venv() -> bool:
    """Return True if we're already running inside a virtual environment."""
    return sys.prefix != sys.base_prefix


def _prepend_sudo(cmd: str) -> str:
    """Prefix each chained command in *cmd* with sudo where needed."""
    parts = re.split(r"(&&|\|\|)", cmd)
    result: list[str] = []
    for part in parts:
        stripped = part.strip()
        if stripped in ("&&", "||"):
            result.append(part)
        elif _PKG_MGR_RE.match(stripped):
            result.append(part.replace(stripped, f"sudo {stripped}", 1))
        else:
            result.append(part)
    return "".join(result)


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

        # When a job uses pip and we're not already in a venv, create a
        # temporary virtual environment so pip install works on
        # externally-managed Python installations (PEP 668).
        venv_dir: str | None = None
        if _script_uses_pip(job.script) and not _in_venv():
            venv_dir = tempfile.mkdtemp(prefix="stitch_venv_")
            combined_log_parts.append(
                f"Stitch: creating temporary venv at {venv_dir}\n",
            )
            import subprocess  # noqa: PLC0415

            venv_ok = False
            # Try stdlib venv first.
            try:
                subprocess.run(
                    [sys.executable, "-m", "venv", venv_dir],
                    check=True,
                    capture_output=True,
                )
                # Verify pip is actually available (Debian ships venv
                # without ensurepip, so the venv may lack pip).
                venv_pip = os.path.join(venv_dir, "bin", "pip")
                if os.path.isfile(venv_pip):
                    venv_ok = True
            except subprocess.CalledProcessError:
                pass

            # Fallback: use uv if stdlib venv has no pip (PEP 668 / Debian).
            if not venv_ok:
                # Clean up partial venv left by failed stdlib attempt.
                shutil.rmtree(venv_dir, ignore_errors=True)
                uv = shutil.which("uv")
                if uv:
                    try:
                        subprocess.run(
                            [uv, "venv", "--seed", venv_dir],
                            check=True,
                            capture_output=True,
                        )
                        venv_ok = True
                    except subprocess.CalledProcessError:
                        pass

            if not venv_ok:
                shutil.rmtree(venv_dir, ignore_errors=True)
                venv_dir = None
            else:
                venv_bin = os.path.join(venv_dir, "bin")
                env["VIRTUAL_ENV"] = venv_dir
                env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
                env.pop("PIP_REQUIRE_VIRTUALENV", None)

        try:
            return await self._run_script(job, env, combined_log_parts, start)
        finally:
            if venv_dir:
                shutil.rmtree(venv_dir, ignore_errors=True)

    async def _run_script(
        self,
        job: CIJob,
        env: dict[str, str],
        combined_log_parts: list[str],
        start: float,
    ) -> ExecResult:
        """Execute job script commands sequentially."""
        remaining = self.timeout_seconds - (time.monotonic() - start)
        for raw_cmd in job.script:
            cmd = _prepend_sudo(raw_cmd) if _needs_sudo(raw_cmd) else raw_cmd
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
