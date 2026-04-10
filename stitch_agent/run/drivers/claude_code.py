"""ClaudeCodeDriver -- delegate fixes to the Claude Code CLI.

Streams stdout line-by-line so the TUI can show what Claude is doing in
real time. Uses --permission-mode acceptEdits so Claude can edit files
without interactive approval.
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
class ClaudeCodeDriver:
    name: str = "claude"
    timeout_seconds: float = 600.0
    binary: str = "claude"
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

        return await self._stream_output(proc)

    async def _stream_output(self, proc: asyncio.subprocess.Process) -> FixOutcome:
        lines: list[str] = []
        assert proc.stdout is not None

        async def read_lines() -> None:
            while True:
                raw = await proc.stdout.readline()  # type: ignore[union-attr]
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")
                lines.append(line)
                if self.on_output:
                    self.on_output("".join(lines))

        try:
            await asyncio.wait_for(read_lines(), timeout=self.timeout_seconds)
            await proc.wait()
        except TimeoutError:
            _kill(proc)
            return FixOutcome(
                applied=False,
                reason=f"{self.binary} CLI timeout after {self.timeout_seconds}s",
                driver_log="".join(lines)[-2000:],
            )

        log = "".join(lines)
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


def _kill(proc: asyncio.subprocess.Process) -> None:
    import contextlib

    with contextlib.suppress(ProcessLookupError):
        proc.kill()
