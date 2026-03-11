from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from anthropic.types import TextBlock

from stitch_agent.models import ClassificationResult, StitchConfig, select_model

_SYSTEM_PROMPT = (
    "You are stitch-agent, an AI that autonomously fixes CI pipeline failures.\n"
    "Your task: analyze a failed CI job and produce a minimal, correct fix.\n"
    "Rules:\n"
    "1. Fix ONLY the specific error shown in the logs. Do not refactor unrelated code.\n"
    "2. Do not add new dependencies or change public APIs.\n"
    "3. Output must be a valid JSON object — no prose, no markdown fences.\n"
    "4. Commit message must follow Conventional Commits: fix(scope): description\n"
    "5. Be conservative: smallest change that fixes the error.\n\n"
    "Response format (strict JSON, no markdown):\n"
    "{\n"
    '  "files": {\n'
    '    "path/to/file.py": "complete new file content"\n'
    "  },\n"
    '  "commit_message": "fix(lint): remove unused import in auth.py",\n'
    '  "explanation": "Two sentences: what was wrong and how it was fixed."\n'
    "}\n\n"
    "Only include files that need to change. The files dict must contain COMPLETE new content."
)

_MAX_LOG_LINES = 150
_MAX_FILE_CHARS = 8000


@dataclass
class FileChange:
    path: str
    new_content: str
    action: Literal["update", "create", "delete"] = "update"


@dataclass
class FixPatch:
    changes: list[FileChange] = field(default_factory=list)
    commit_message: str = ""
    explanation: str = ""


class Fixer:
    def __init__(self, anthropic_api_key: str, config: StitchConfig | None = None) -> None:
        self.anthropic_api_key = anthropic_api_key
        self.config = config or StitchConfig()
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self.anthropic_api_key)
        return self._client

    async def generate_fix(
        self,
        classification: ClassificationResult,
        job_log: str,
        diff: str,
        file_contents: dict[str, str] | None = None,
    ) -> FixPatch:
        model = select_model(classification.error_type)
        prompt = _build_prompt(classification, job_log, diff, file_contents or {})
        client = self._get_client()
        message = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next((b.text for b in message.content if isinstance(b, TextBlock)), "")
        return _parse_response(raw)


def _build_prompt(
    classification: ClassificationResult,
    job_log: str,
    diff: str,
    file_contents: dict[str, str],
) -> str:
    log_lines = job_log.splitlines()
    if len(log_lines) > _MAX_LOG_LINES:
        log_lines = log_lines[:20] + ["... (truncated) ..."] + log_lines[-(_MAX_LOG_LINES - 20) :]
    truncated_log = "\n".join(log_lines)

    files_section = ""
    if file_contents:
        parts = []
        for path, content in file_contents.items():
            truncated = content[:_MAX_FILE_CHARS]
            if len(content) > _MAX_FILE_CHARS:
                truncated += "\n... (truncated)"
            parts.append(f"### {path}\n```\n{truncated}\n```")
        files_section = "\n\n## Current file contents\n" + "\n\n".join(parts)

    affected = ", ".join(classification.affected_files) or "unknown"
    return (
        f"## Error classification\n"
        f"Type: {classification.error_type.value}\n"
        f"Confidence: {classification.confidence:.0%}\n"
        f"Summary: {classification.summary}\n"
        f"Affected files: {affected}\n"
        f"\n## Failed job log\n```\n{truncated_log}\n```"
        f"\n\n## Diff that triggered this pipeline\n```diff\n{diff or '(no diff available)'}\n```"
        f"{files_section}"
        f"\n\nAnalyze the error and produce the minimal fix. Respond with JSON only."
    )


def _parse_response(raw: str) -> FixPatch:
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence_match:
        text = fence_match.group(1)
    else:
        brace_match = re.search(r"\{.*\}", text, re.S)
        if brace_match:
            text = brace_match.group(0)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return FixPatch(
            changes=[],
            commit_message="fix: automated fix by stitch-agent",
            explanation=f"Could not parse model response. Raw: {raw[:200]}",
        )

    files_dict = data.get("files", {})
    changes = [
        FileChange(path=path, new_content=content)
        for path, content in files_dict.items()
        if isinstance(path, str) and isinstance(content, str)
    ]
    return FixPatch(
        changes=changes,
        commit_message=data.get("commit_message", "fix: automated fix by stitch-agent"),
        explanation=data.get("explanation", "Fix applied by stitch-agent"),
    )
