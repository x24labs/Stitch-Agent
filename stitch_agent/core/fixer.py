from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from anthropic.types import TextBlock

from stitch_agent.models import ClassificationResult, ErrorType, StitchConfig, select_model

_BASE_PREAMBLE = (
    "You are stitch-agent, an AI that autonomously fixes CI pipeline failures.\n"
    "Your task: analyze a failed CI job and produce a minimal, correct fix.\n\n"
)

_RESPONSE_FORMAT = (
    "Response format (strict JSON, no markdown):\n"
    "{\n"
    '  "files": {\n'
    '    "path/to/file.py": "complete new file content"\n'
    "  },\n"
    '  "commit_message": "fix(scope): description of the fix",\n'
    '  "explanation": "Two sentences: what was wrong and how it was fixed."\n'
    "}\n\n"
    "Only include files that need to change. The files dict must contain COMPLETE new content.\n"
    "IMPORTANT: Do NOT include line numbers in the file content — output raw source only.\n"
    "If the fix is truly impossible (would require wholesale architecture changes), "
    "return an empty files dict and explain why."
)

_STRICT_CONSTRAINTS = (
    "Rules:\n"
    "1. Fix ONLY the specific error shown in the logs. Do not refactor unrelated code.\n"
    "2. Do not add new dependencies.\n"
    "3. Output must be a valid JSON object — no prose, no markdown fences.\n"
    "4. Commit message must follow Conventional Commits: fix(scope): description\n"
    "5. Be conservative: smallest change that fixes the error.\n\n"
    "CRITICAL constraints — violating these will break other code:\n"
    "- NEVER change function signatures (parameter names, types, count, or defaults).\n"
    "- NEVER change type definitions, interfaces, or exported types.\n"
    "- NEVER rename or remove exports that other files may import.\n"
    "- NEVER modify lines unrelated to the error, even if they look improvable.\n"
    "- Only touch the exact lines that cause the reported error.\n"
    "- If fixing the error requires changing a function signature, that means the fix is\n"
    "  too complex — return an empty files dict and explain why in the explanation field.\n\n"
)

_TEST_CONSTRAINTS = (
    "Rules:\n"
    "1. Fix the specific error shown in the logs. Focus on the root cause, not symptoms.\n"
    "2. Output must be a valid JSON object — no prose, no markdown fences.\n"
    "3. Commit message must follow Conventional Commits: fix(scope): description\n"
    "4. Prefer fixing SOURCE code over modifying tests, unless the test itself is wrong.\n"
    "5. You MAY add missing imports if the error is an ImportError or NameError.\n"
    "6. You MAY fix function calls if arguments are wrong (missing, extra, wrong type).\n"
    "7. Do NOT refactor unrelated code or make stylistic changes.\n"
    "8. Do NOT add new external package dependencies (stdlib and existing deps are fine).\n\n"
    "Strategy for test failures:\n"
    "- Read the traceback carefully to identify the SOURCE file and line that fails.\n"
    "- If the test shows an ImportError/ModuleNotFoundError, fix the import path or\n"
    "  ensure the module exists with the expected symbols.\n"
    "- If the test shows AttributeError/NameError, fix the missing attribute/name in\n"
    "  the source file.\n"
    "- If an assertion fails because of changed behavior, fix the source code to\n"
    "  match the expected contract, NOT the test.\n"
    "- ALWAYS produce a fix. An empty files dict means the CI stays broken.\n\n"
)

_BUILD_CONSTRAINTS = (
    "Rules:\n"
    "1. Fix the specific build/infrastructure error shown in the logs.\n"
    "2. Output must be a valid JSON object — no prose, no markdown fences.\n"
    "3. Commit message must follow Conventional Commits: fix(scope): description\n"
    "4. You MAY modify config files (pyproject.toml, setup.cfg, requirements.txt,\n"
    "   Dockerfile, .gitlab-ci.yml, package.json) to fix build issues.\n"
    "5. You MAY add missing dependencies to pyproject.toml/requirements.txt if the\n"
    "   error is a missing package.\n"
    "6. You MAY fix shell commands, paths, or environment setup in CI config.\n"
    "7. Do NOT modify source code unless the build error originates from source.\n"
    "8. ALWAYS produce a fix. An empty files dict means the CI stays broken.\n\n"
)

_CONSTRAINT_MAP: dict[ErrorType, str] = {
    ErrorType.LINT: _STRICT_CONSTRAINTS,
    ErrorType.FORMAT: _STRICT_CONSTRAINTS,
    ErrorType.SIMPLE_TYPE: _STRICT_CONSTRAINTS,
    ErrorType.CONFIG_CI: _BUILD_CONSTRAINTS,
    ErrorType.BUILD: _BUILD_CONSTRAINTS,
    ErrorType.COMPLEX_TYPE: _TEST_CONSTRAINTS,
    ErrorType.TEST_CONTRACT: _TEST_CONSTRAINTS,
    ErrorType.LOGIC_ERROR: _TEST_CONSTRAINTS,
    ErrorType.UNKNOWN: _TEST_CONSTRAINTS,
}


def _get_system_prompt(error_type: ErrorType) -> str:
    constraints = _CONSTRAINT_MAP.get(error_type, _STRICT_CONSTRAINTS)
    return f"{_BASE_PREAMBLE}{constraints}{_RESPONSE_FORMAT}"

_MAX_LOG_LINES = 200
_MAX_FILE_CHARS = 48_000

_ERROR_SECTION_RE = re.compile(
    r"("
    r"Traceback \(most recent call last\)"
    r"|^E\s+"
    r"|FAILED\s+\S+"
    r"|ERROR\s+(collecting|at setup|at teardown)"
    r"|\b(AssertionError|ModuleNotFoundError|ImportError|NameError|TypeError"
    r"|AttributeError|KeyError|ValueError|RuntimeError|FileNotFoundError)\b"
    r"|={3,}\s*(ERRORS|FAILURES|short test summary)"
    r"|\d+ (failed|error)"
    r")",
    re.M | re.I,
)


def _smart_truncate_log(job_log: str, max_lines: int = _MAX_LOG_LINES) -> str:
    """Truncate log preserving error/traceback sections.

    Instead of blindly keeping first 20 + last 130 lines, identifies
    error-relevant sections and prioritizes them.
    """
    lines = job_log.splitlines()
    if len(lines) <= max_lines:
        return job_log

    important_indices: set[int] = set()
    for i, line in enumerate(lines):
        if _ERROR_SECTION_RE.search(line):
            # Keep context: 3 lines before, 5 lines after each error line
            for j in range(max(0, i - 3), min(len(lines), i + 6)):
                important_indices.add(j)

    # Always keep first 10 (setup/env) and last 20 (summary) lines
    head = set(range(min(10, len(lines))))
    tail = set(range(max(0, len(lines) - 20), len(lines)))
    important_indices = head | important_indices | tail

    if len(important_indices) > max_lines:
        # Too many important lines — fall back to prioritizing error sections + tail
        sorted_idx = sorted(important_indices)
        important_indices = set(sorted_idx[-max_lines:])
        important_indices |= head

    sorted_indices = sorted(important_indices)
    result: list[str] = []
    prev = -1
    for idx in sorted_indices:
        if prev >= 0 and idx > prev + 1:
            skipped = idx - prev - 1
            result.append(f"... ({skipped} lines omitted) ...")
        result.append(lines[idx])
        prev = idx

    return "\n".join(result)


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
        *,
        model_override: str | None = None,
    ) -> FixPatch:
        model = model_override or select_model(classification.error_type)
        system_prompt = _get_system_prompt(classification.error_type)
        prompt = _build_prompt(classification, job_log, diff, file_contents or {})
        client = self._get_client()
        message = await client.messages.create(
            model=model,
            max_tokens=16_384,
            system=system_prompt,
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
    truncated_log = _smart_truncate_log(job_log)

    files_section = ""
    if file_contents:
        parts = []
        for path, content in file_contents.items():
            truncated = content[:_MAX_FILE_CHARS]
            was_truncated = len(content) > _MAX_FILE_CHARS
            # Add line numbers so the LLM can locate error lines from the log
            numbered = []
            for i, line in enumerate(truncated.splitlines(), 1):
                numbered.append(f"{i:>4}| {line}")
            display = "\n".join(numbered)
            if was_truncated:
                display += "\n... (truncated)"
            parts.append(f"### {path}\n```\n{display}\n```")
        files_section = "\n\n## Current file contents (line numbers for reference only — do NOT include them in output)\n" + "\n\n".join(parts)

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
