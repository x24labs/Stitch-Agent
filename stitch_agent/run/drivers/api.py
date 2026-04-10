"""ApiDriver -- fallback driver that calls an LLM API directly.

For users who don't have a Claude Code or Codex CLI subscription. Makes a
single chat completion call via the OpenAI-compatible API (e.g. OpenRouter),
parses a JSON response with file changes, and writes them to disk.

No dependency on stitch_agent.core or stitch_agent.adapters.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from stitch_agent.run.drivers.base import build_prompt
from stitch_agent.run.models import FixContext, FixOutcome

_MODEL = "google/gemini-2.5-flash"

_SYSTEM = (
    "You are a CI fix agent. You receive a failing CI job log and must "
    "produce a minimal fix.\n\n"
    "Respond ONLY with a JSON object:\n"
    "{\n"
    '  "changes": [\n'
    '    {"path": "relative/path/to/file", "content": "complete new file content"}\n'
    "  ],\n"
    '  "explanation": "What was wrong and what you changed"\n'
    "}\n\n"
    "Rules:\n"
    "- Fix ONLY the specific error shown in the log.\n"
    "- Each change must contain the COMPLETE new file content.\n"
    "- Do NOT wrap content in markdown fences.\n"
    "- Be conservative: smallest change that fixes the error.\n"
    "- If you cannot fix it, return an empty changes array and explain why.\n"
)


@dataclass
class ApiDriver:
    name: str = "api"
    api_key: str = ""
    base_url: str | None = None
    model: str = _MODEL

    async def fix(self, context: FixContext) -> FixOutcome:
        if not self.api_key:
            return FixOutcome(applied=False, reason="STITCH_LLM_API_KEY not set")

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        prompt = build_prompt(context)

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            return FixOutcome(applied=False, reason=f"LLM API error: {exc}")

        raw = (response.choices[0].message.content or "").strip()
        changes, explanation = _parse_response(raw)

        if not changes:
            return FixOutcome(applied=False, reason=explanation or "LLM produced no changes")

        for path, content in changes:
            full = (context.repo_root / path).resolve()
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)

        return FixOutcome(applied=True, reason=explanation[:200])


def _parse_response(raw: str) -> tuple[list[tuple[str, str]], str]:
    """Parse LLM JSON response into [(path, content)] and explanation."""
    text = raw
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            text = brace.group(0)

    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [], f"Could not parse LLM response: {raw[:100]}"

    if not isinstance(data, dict):
        return [], f"Expected JSON object, got {type(data).__name__}"

    explanation: str = data.get("explanation", "fix applied")
    raw_changes = data.get("changes", [])
    if not isinstance(raw_changes, list):
        return [], explanation

    changes: list[tuple[str, str]] = []
    for entry in raw_changes:
        if isinstance(entry, dict):
            path = entry.get("path", "")
            content = entry.get("content", "")
            if isinstance(path, str) and isinstance(content, str) and path:
                changes.append((path, content))

    return changes, explanation
