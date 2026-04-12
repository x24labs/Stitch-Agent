"""CodexDriver — experimental driver for the OpenAI Codex CLI.

Marked experimental because the `codex` CLI interface is newer and may change.
Shares the same contract as ClaudeCodeDriver.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from stitch_agent.run.drivers.base import build_prompt
from stitch_agent.run.models import FixContext, FixOutcome

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class CodexDriver:
    name: str = "codex"
    timeout_seconds: float = 600.0
    binary: str = "codex"
    on_output: Callable[[str], None] | None = field(default=None, repr=False)

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
                "exec",
                prompt,
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
