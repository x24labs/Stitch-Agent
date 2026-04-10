"""ClaudeCodeDriver — delegate fixes to the Claude Code CLI.

The Claude Code CLI is assumed to be installed as `claude` in PATH. We invoke
it in non-interactive mode with `claude -p <prompt>` and let it edit files
directly in the repo. The runner then re-runs the failing job and uses that as
the ground truth for whether the fix worked.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from stitch_agent.run.drivers.base import build_prompt
from stitch_agent.run.models import FixContext, FixOutcome


@dataclass
class ClaudeCodeDriver:
    name: str = "claude"
    timeout_seconds: float = 600.0
    binary: str = "claude"

    async def fix(self, context: FixContext) -> FixOutcome:
        if not shutil.which(self.binary):
            return FixOutcome(
                applied=False,
                reason=f"{self.binary} CLI not found in PATH",
            )

        prompt = build_prompt(context)
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "-p",
                prompt,
                "--permission-mode",
                "acceptEdits",
                cwd=str(context.repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            return FixOutcome(
                applied=False,
                reason=f"{self.binary} CLI not found in PATH",
            )
        except Exception as exc:
            return FixOutcome(
                applied=False,
                reason=f"failed to spawn {self.binary}: {exc}",
            )

        try:
            out_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds,
            )
        except TimeoutError:
            with _suppress():
                proc.kill()
            with _suppress():
                await proc.wait()
            return FixOutcome(
                applied=False,
                reason=f"{self.binary} CLI timeout after {self.timeout_seconds}s",
            )

        log = (out_bytes or b"").decode("utf-8", errors="replace")
        log_tail = log[-2000:]

        if proc.returncode != 0:
            return FixOutcome(
                applied=False,
                reason=f"{self.binary} exited {proc.returncode}",
                driver_log=log_tail,
            )

        return FixOutcome(
            applied=True,
            reason=f"{self.binary} CLI completed",
            driver_log=log_tail,
        )


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return True
