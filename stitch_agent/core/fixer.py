"""Agentic fixer — uses tool use to investigate and fix CI failures.

Instead of a single-shot LLM call with pre-fetched files, the fixer
gives the LLM tools to search, read, and explore the codebase so it
can find and fix the root cause autonomously.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from openai import AsyncOpenAI

from stitch_agent.models import (
    ClassificationResult,
    ErrorType,
    StitchConfig,
    UsageStats,
    select_model,
)

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

    from stitch_agent.adapters.base import CIPlatformAdapter
    from stitch_agent.models import FixRequest

logger = logging.getLogger("stitch_agent")

_MAX_TOOL_ROUNDS = 15
_FAST_FIX_TYPES: frozenset[ErrorType] = frozenset({ErrorType.FORMAT, ErrorType.LINT})
_MAX_LOG_LINES = 200

_SYSTEM_PROMPT = (
    "You are stitch-agent, an AI that autonomously fixes CI pipeline failures.\n"
    "You have tools to investigate the codebase. Use them to understand the error,\n"
    "find the relevant files, and produce a minimal fix.\n\n"
    "Workflow:\n"
    "1. Read the error log carefully to understand what failed.\n"
    "2. Use search_codebase to find where the problematic code/config lives.\n"
    "3. Use read_file to read the files you need to understand and fix.\n"
    "4. Once you understand the root cause, produce the fix.\n\n"
    "When you are ready to produce the fix, respond with JSON (no tools):\n"
    "{\n"
    '  "files": {\n'
    '    "path/to/file": "complete new file content"\n'
    "  },\n"
    '  "commit_message": "fix(scope): description",\n'
    '  "explanation": "What was wrong and how it was fixed."\n'
    "}\n\n"
    "Rules:\n"
    "- Fix ONLY the specific error shown in the logs.\n"
    "- The files dict must contain COMPLETE new content for each file you change.\n"
    "- Do NOT include line numbers in file content.\n"
    "- Do NOT wrap file content in markdown fences (no ```python or ```). Values must be raw source code.\n"
    "- Commit message must follow Conventional Commits: fix(scope): description\n"
    "- Be conservative: smallest change that fixes the error.\n"
    "- If the error comes from a CI command (wrong argument, missing tool), fix the CI config.\n"
    "- If the fix is truly impossible, return an empty files dict and explain why.\n"
    "- ALWAYS produce a fix. An empty files dict means the CI stays broken.\n"
)

_TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": (
                "Search for a pattern in the codebase. Returns matching file paths, "
                "line numbers, and snippets. Use this to find where a specific string, "
                "flag, function, or config option is defined or used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (e.g. '--cov-data-file', 'def broken_func', 'COVERAGE_FILE')",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the full content of a file. Use this to understand the code "
                "before producing a fix. Returns the file content with line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root (e.g. '.gitlab-ci.yml', 'src/main.py')",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List files and directories at a given path. Use this to explore "
                "the repository structure when you need to find config files or source directories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (empty string for repo root)",
                    },
                },
                "required": ["path"],
            },
        },
    },
]


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
    r"|error: unrecognized arguments?"
    r"|pytest: error:"
    r")",
    re.M | re.I,
)


def _smart_truncate_log(job_log: str, max_lines: int = _MAX_LOG_LINES) -> str:
    """Truncate log preserving error/traceback sections."""
    lines = job_log.splitlines()
    if len(lines) <= max_lines:
        return job_log

    important_indices: set[int] = set()
    for i, line in enumerate(lines):
        if _ERROR_SECTION_RE.search(line):
            for j in range(max(0, i - 3), min(len(lines), i + 6)):
                important_indices.add(j)

    head = set(range(min(10, len(lines))))
    tail = set(range(max(0, len(lines) - 20), len(lines)))
    important_indices = head | important_indices | tail

    if len(important_indices) > max_lines:
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
    usage: UsageStats = field(default_factory=UsageStats)


def _extract_usage(response: object) -> UsageStats:
    """Extract token usage and generation ID from an OpenAI-compatible API response."""
    usage = getattr(response, "usage", None)
    gen_id = getattr(response, "id", None)
    gen_ids = [gen_id] if gen_id and isinstance(gen_id, str) else []
    if usage is None:
        return UsageStats(generation_ids=gen_ids)
    return UsageStats(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        generation_ids=gen_ids,
    )


class Fixer:
    def __init__(
        self,
        api_key: str,
        config: StitchConfig | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.config = config or StitchConfig()
        self._base_url = base_url
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self._base_url,
            )
        return self._client

    async def generate_fix(
        self,
        classification: ClassificationResult,
        job_log: str,
        diff: str,
        file_contents: dict[str, str] | None = None,
        *,
        model_override: str | None = None,
        adapter: CIPlatformAdapter | None = None,
        request: FixRequest | None = None,
        force_tools: bool = False,
    ) -> FixPatch:
        model = model_override or select_model(
            classification.error_type,
            light=self.config.models.light,
            heavy=self.config.models.heavy,
        )
        client = self._get_client()

        # Build initial prompt with error context
        truncated_log = _smart_truncate_log(job_log)
        prompt = (
            f"## Error classification\n"
            f"Type: {classification.error_type.value}\n"
            f"Confidence: {classification.confidence:.0%}\n"
            f"Summary: {classification.summary}\n"
            f"Affected files: {', '.join(classification.affected_files) or 'unknown'}\n"
            f"\n## Failed job log\n```\n{truncated_log}\n```"
            f"\n\n## Diff that triggered this pipeline\n"
            f"```diff\n{diff or '(no diff available)'}\n```"
        )

        # Include pre-fetched file contents when available
        if file_contents:
            parts = []
            for path, content in file_contents.items():
                numbered = []
                for i, line in enumerate(content[:48_000].splitlines(), 1):
                    numbered.append(f"{i:>4}| {line}")
                parts.append(f"### {path}\n```\n" + "\n".join(numbered) + "\n```")
            prompt += (
                "\n\n## File contents (line numbers for reference"
                " — do NOT include in output)\n" + "\n\n".join(parts)
            )

        is_fast_fix = classification.error_type in _FAST_FIX_TYPES and not force_tools

        if is_fast_fix:
            prompt += (
                "\n\nThis is a FORMAT/LINT error. The affected file contents are "
                "already provided above. Do NOT search or read files — just produce "
                "the fix JSON immediately.\n\n"
                "CRITICAL formatting rules (apply ALL of these to EVERY line):\n"
                "- Maximum line length: 88 characters (black default)\n"
                "- Break ALL lines exceeding 88 chars, not just some\n"
                "- For function calls: put each argument on its own line if the call exceeds 88 chars\n"
                "- For function definitions: put each parameter on its own line if the def exceeds 88 chars\n"
                "- Closing paren/bracket goes on its own line, aligned with the opening statement\n"
                "- Use trailing commas after the last argument in multi-line calls\n"
                "- Collapse single-line docstrings: '''\\nText.\\n''' → '''Text.'''\n"
                "- Scan the ENTIRE file for violations, not just the obvious ones\n"
                "- You MUST return the COMPLETE file content with ALL formatting fixed\n"
                "- A partial fix that misses even one line will cause the CI to fail again\n"
            )
        else:
            prompt += "\n\nInvestigate the error and produce the minimal fix."

        # If adapter available, use agentic tool-use mode
        if adapter and request:
            return await self._agentic_fix(
                client, model, prompt, adapter, request,
                skip_tools=is_fast_fix,
            )

        # Fallback: single-shot (backward compatible)
        return await self._single_shot_fix(client, model, prompt)

    async def _single_shot_fix(
        self, client: AsyncOpenAI, model: str, prompt: str,
    ) -> FixPatch:
        """Single-shot fix without tools (backward compatible)."""
        response = await client.chat.completions.create(
            model=model,
            max_tokens=16_384,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        raw = response.choices[0].message.content or ""
        patch = _parse_response(raw)
        patch.usage += _extract_usage(response)
        return patch

    async def _agentic_fix(
        self,
        client: AsyncOpenAI,
        model: str,
        prompt: str,
        adapter: CIPlatformAdapter,
        request: FixRequest,
        *,
        skip_tools: bool = False,
    ) -> FixPatch:
        """Agentic fix with tool use — the LLM investigates before fixing."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        # Fast path: FORMAT/LINT errors already have file contents in the prompt,
        # so skip tools entirely and let the LLM produce the fix in one shot.
        if skip_tools:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=16_384,
                messages=messages,
            )
            finish_reason = response.choices[0].finish_reason
            raw = response.choices[0].message.content or ""

            if finish_reason == "length":
                # Response was truncated — JSON is incomplete and unparseable.
                # Fall through to agentic mode with tools so the model reads/fixes
                # files incrementally rather than outputting everything at once.
                logger.warning(
                    "fast-path response truncated (finish_reason=length), "
                    "falling back to agentic mode"
                )
                # Continue below into the agentic loop (skip_tools is now ignored)
            elif raw:
                logger.info("fast-path fix for format/lint error (no tools needed)")
                patch = _parse_response(raw)
                patch.usage += _extract_usage(response)
                return patch
            else:
                return FixPatch(
                    changes=[],
                    commit_message="fix: automated fix by stitch-agent",
                    explanation="Fast-path fix produced no output",
                    usage=_extract_usage(response),
                )

        cumulative_usage = UsageStats()
        for round_num in range(_MAX_TOOL_ROUNDS):
            response = await client.chat.completions.create(
                model=model,
                max_tokens=16_384,
                messages=messages,
                tools=_TOOLS,
            )
            cumulative_usage += _extract_usage(response)

            choice = response.choices[0]

            # Check if the LLM is done (no tool calls — produced text with the fix)
            if choice.finish_reason == "stop" or not choice.message.tool_calls:
                raw = choice.message.content or ""
                if raw:
                    logger.info("agentic fix completed after %d tool rounds", round_num)
                    patch = _parse_response(raw)
                    patch.usage += cumulative_usage
                    return patch
                if not choice.message.tool_calls:
                    break

            # Process tool calls
            tool_calls = choice.message.tool_calls or []

            # Add assistant message to conversation
            messages.append({"role": "assistant", "content": choice.message.content, "tool_calls": [tc.model_dump() for tc in tool_calls]})  # type: ignore[typeddict-item]

            # Execute tools and collect results
            for tool_call in tool_calls:
                fn = tool_call.function  # type: ignore[union-attr]
                try:
                    args = json.loads(fn.arguments)
                except json.JSONDecodeError:
                    args = {}

                result = await _execute_tool(fn.name, args, adapter, request)
                logger.debug(
                    "tool %s(%s) → %d chars",
                    fn.name,
                    fn.arguments[:100],
                    len(result),
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result[:16_000],
                })

        logger.warning("agentic fix exhausted %d tool rounds", _MAX_TOOL_ROUNDS)
        return FixPatch(
            changes=[],
            commit_message="fix: automated fix by stitch-agent",
            explanation="Exhausted tool rounds without producing a fix",
            usage=cumulative_usage,
        )


async def _execute_tool(
    name: str, input_data: dict, adapter: CIPlatformAdapter, request: FixRequest,
) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "search_codebase":
            results = await adapter.search_codebase(request, input_data["pattern"])
            if not results:
                return "No results found."
            lines = []
            for r in results:
                line_info = f":{r['line']}" if r.get("line") else ""
                lines.append(f"{r['path']}{line_info}: {r.get('data', '')}")
            return "\n".join(lines)

        if name == "read_file":
            content = await adapter.fetch_file_content(request, input_data["path"])
            # Add line numbers for reference
            numbered = []
            for i, line in enumerate(content.splitlines(), 1):
                numbered.append(f"{i:>4}| {line}")
            return "\n".join(numbered)

        if name == "list_directory":
            items = await adapter.list_directory(request, input_data.get("path", ""))
            lines = []
            for item in items:
                prefix = "dir  " if item["type"] == "tree" else "file "
                lines.append(f"{prefix}{item['path']}")
            return "\n".join(lines) or "Empty directory."

        return f"Unknown tool: {name}"
    except Exception as exc:
        return f"Error: {exc}"


def _strip_content_fences(content: str) -> str:
    """Strip markdown code fences that LLMs sometimes wrap around file content."""
    stripped = content.strip()
    match = re.match(r"^```\w*\n(.*?)```\s*$", stripped, re.S)
    if match:
        return match.group(1)
    return content


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
        # Models sometimes return Python-style dicts (single quotes, True/False).
        # Try ast.literal_eval as a fallback before giving up.
        try:
            data = ast.literal_eval(text)
        except Exception:
            return FixPatch(
                changes=[],
                commit_message="fix: automated fix by stitch-agent",
                explanation=f"Could not parse model response. Raw: {raw[:200]}",
            )

    files_dict = data.get("files", {})
    changes = [
        FileChange(path=path, new_content=_strip_content_fences(content))
        for path, content in files_dict.items()
        if isinstance(path, str) and isinstance(content, str)
    ]
    return FixPatch(
        changes=changes,
        commit_message=data.get("commit_message", "fix: automated fix by stitch-agent"),
        explanation=data.get("explanation", "Fix applied by stitch-agent"),
    )
