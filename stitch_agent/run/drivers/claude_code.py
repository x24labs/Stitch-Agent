"""ClaudeCodeDriver -- delegate fixes to the Claude Code CLI.

Uses --output-format stream-json for real-time streaming of Claude's actions
into the TUI. Each line of stdout is a JSON event; we extract assistant text,
tool use, and tool results to build a human-readable activity log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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
                "--output-format",
                "stream-json",
                "--verbose",
                cwd=str(context.repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
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
        activity: list[str] = []
        result_text = ""
        assert proc.stdout is not None

        async def read_events() -> None:
            nonlocal result_text
            while True:
                raw = await proc.stdout.readline()  # type: ignore[union-attr]
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                event = _parse_event(line)
                if event is None:
                    continue

                if event["kind"] == "text":
                    activity.append(event["content"])
                    self._emit(activity)
                elif event["kind"] == "tool_use":
                    activity.append(f"> {event['content']}")
                    self._emit(activity)
                elif event["kind"] == "tool_result":
                    # Show a truncated preview of the result
                    preview = event["content"][:200]
                    if len(event["content"]) > 200:
                        preview += "..."
                    activity.append(f"  {preview}")
                    self._emit(activity)
                elif event["kind"] == "result":
                    result_text = event["content"]

        try:
            await asyncio.wait_for(read_events(), timeout=self.timeout_seconds)
            await proc.wait()
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return FixOutcome(
                applied=False,
                reason=f"{self.binary} CLI timeout after {self.timeout_seconds}s",
                driver_log="\n".join(activity)[-2000:],
            )

        log = "\n".join(activity)

        if proc.returncode != 0:
            return FixOutcome(
                applied=False,
                reason=f"{self.binary} exited {proc.returncode}",
                driver_log=log[-2000:],
            )

        reason = result_text[:200] if result_text else f"{self.binary} CLI completed"
        return FixOutcome(
            applied=True,
            reason=reason,
            driver_log=log[-2000:],
        )

    def _emit(self, activity: list[str]) -> None:
        if self.on_output:
            self.on_output("\n".join(activity))


def _parse_event(line: str) -> dict[str, str] | None:
    """Extract a human-readable event from a stream-json line."""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    event_type = data.get("type")

    if event_type == "assistant":
        msg = data.get("message", {})
        content_blocks = msg.get("content", [])
        parts: list[str] = []
        for block in content_blocks:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
            elif block.get("type") == "tool_use":
                tool_name = block.get("name", "tool")
                tool_input = block.get("input", {})
                desc = tool_input.get("description") or tool_input.get("command") or ""
                if desc:
                    return {"kind": "tool_use", "content": f"{tool_name}: {desc}"}
                # For tools like Edit, show the file path
                path = tool_input.get("file_path") or tool_input.get("path") or ""
                pattern = tool_input.get("pattern") or tool_input.get("query") or ""
                if path:
                    return {"kind": "tool_use", "content": f"{tool_name}: {path}"}
                if pattern:
                    return {"kind": "tool_use", "content": f"{tool_name}: {pattern}"}
                return {"kind": "tool_use", "content": tool_name}
        if parts:
            return {"kind": "text", "content": " ".join(parts)}
        return None

    if event_type == "user":
        # Tool results come back as user messages
        msg = data.get("message", {})
        content_blocks = msg.get("content", [])
        for block in content_blocks:
            if block.get("type") == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = " ".join(
                        b.get("text", "") for b in result_content if isinstance(b, dict)
                    )
                if isinstance(result_content, str) and result_content.strip():
                    return {"kind": "tool_result", "content": result_content.strip()}
        return None

    if event_type == "result":
        return {"kind": "result", "content": data.get("result", "")}

    return None
