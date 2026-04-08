"""Agentic error classifier — uses an LLM to analyze CI job logs.

Replaces regex-based classification with an LLM call for accurate
error type detection, affected file extraction, and model selection.
"""

from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI


def _normalize_path(path: str) -> str:
    """Strip absolute CI build prefixes so paths are relative to the repo root.

    GitLab CI: /builds/<namespace>/<project>/src/foo.ts -> src/foo.ts
    GitHub Actions: /home/runner/work/<repo>/<repo>/src/foo.ts -> src/foo.ts
    """
    m = re.match(r"^/builds/[^/]+/[^/]+/(.+)$", path)
    if m:
        return m.group(1)
    m = re.match(r"^/home/runner/work/[^/]+/[^/]+/(.+)$", path)
    if m:
        return m.group(1)
    m = re.match(r"^/workspace/[^/]+/(.+)$", path)
    if m:
        return m.group(1)
    return path

from stitch_agent.models import (
    ClassificationResult,
    ErrorType,
    StitchConfig,
    UsageStats,
)

logger = logging.getLogger("stitch_agent")

_CLASSIFY_SYSTEM = (
    "You are a CI/CD error classifier. Analyze the job log and classify the error.\n\n"
    "Error types:\n"
    "- lint: linter violations (ruff, eslint, pylint, unused imports, style)\n"
    "- format: code formatting issues (black, prettier, isort)\n"
    "- simple_type: basic type errors (mypy, pyright, TypeScript — simple mismatches)\n"
    "- complex_type: advanced type errors (generics, protocols, overloads)\n"
    "- config_ci: CI/CD configuration errors (YAML syntax, pipeline config)\n"
    "- build: build/infrastructure errors (missing commands, bad arguments, dependency install failures)\n"
    "- test_contract: test failures (assertion errors, broken test expectations)\n"
    "- logic_error: runtime errors (tracebacks, exceptions, segfaults)\n"
    "- unknown: cannot determine\n\n"
    "Respond with JSON only:\n"
    "{\n"
    '  "error_type": "one of the types above",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "summary": "one-line description of the error",\n'
    '  "affected_files": ["file paths that need to be fixed"],\n'
    '  "model": "light" or "heavy"\n'
    "}\n\n"
    "Rules for affected_files:\n"
    "- Include the ACTUAL files that need to change to fix the error.\n"
    "- If the error is in a CI command (e.g. wrong argument to pytest), include .gitlab-ci.yml\n"
    "- If the error references specific source files, include those.\n"
    "- Include config files (pyproject.toml, package.json) when relevant.\n"
    "- Do NOT include files that are only mentioned in stack traces but don't need changes.\n\n"
    "Rules for model selection:\n"
    "- light: lint, format, simple_type, config_ci, build (straightforward fixes)\n"
    "- heavy: complex_type, test_contract, logic_error, unknown (need deeper reasoning)\n"
)

_MAX_LOG_CHARS = 12_000


class Classifier:
    def __init__(
        self,
        config: StitchConfig | None = None,
        api_key: str = "",
        base_url: str | None = None,
    ) -> None:
        self.config = config or StitchConfig()
        self._api_key = api_key
        self._base_url = base_url
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
        return self._client

    async def classify(self, job_log: str, diff: str | None = None) -> ClassificationResult:
        """Classify a CI job failure using the classifier model."""
        if not self._api_key:
            logger.warning("No API key for agentic classifier, falling back to regex")
            return _regex_fallback(job_log)

        try:
            return await self._llm_classify(job_log, diff)
        except Exception as exc:
            logger.warning("LLM classifier failed (%s), falling back to regex", exc)
            return _regex_fallback(job_log)

    async def _llm_classify(self, job_log: str, diff: str | None) -> ClassificationResult:
        # Send the tail of the log (where errors typically are)
        log_tail = job_log[-_MAX_LOG_CHARS:] if len(job_log) > _MAX_LOG_CHARS else job_log

        prompt = f"## CI Job Log (last {len(log_tail)} chars)\n```\n{log_tail}\n```"
        if diff:
            prompt += f"\n\n## Diff that triggered this pipeline\n```diff\n{diff[:4000]}\n```"

        model = self.config.models.classifier
        client = self._get_client()
        response = await client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )

        raw = response.choices[0].message.content or ""
        result = _parse_classification(raw)
        usage = getattr(response, "usage", None)
        gen_id = getattr(response, "id", None)
        gen_ids = [gen_id] if gen_id and isinstance(gen_id, str) else []
        if usage:
            result.usage = UsageStats(
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
                generation_ids=gen_ids,
            )
        elif gen_ids:
            result.usage = UsageStats(generation_ids=gen_ids)
        return result


def _parse_classification(raw: str) -> ClassificationResult:
    """Parse LLM classification response into ClassificationResult."""
    text = raw.strip()
    # Extract JSON from fences if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            text = brace.group(0)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ClassificationResult(
            error_type=ErrorType.UNKNOWN,
            confidence=0.5,
            summary="Could not parse classifier response",
            affected_files=[],
        )

    # Map error_type string to enum
    error_type_str = data.get("error_type", "unknown")
    try:
        error_type = ErrorType(error_type_str)
    except ValueError:
        error_type = ErrorType.UNKNOWN

    confidence = min(max(float(data.get("confidence", 0.5)), 0.0), 0.99)
    summary = data.get("summary", "CI job failed")
    raw_files = [f for f in data.get("affected_files", []) if isinstance(f, str)]
    affected_files = [_normalize_path(f) for f in raw_files]

    return ClassificationResult(
        error_type=error_type,
        confidence=round(confidence, 2),
        summary=summary,
        affected_files=affected_files[:20],
    )


# ---------------------------------------------------------------------------
# Regex fallback (used when no API key or LLM call fails)
# ---------------------------------------------------------------------------

_FALLBACK_RULES: dict[ErrorType, list[tuple[re.Pattern[str], float]]] = {
    ErrorType.FORMAT: [
        (re.compile(r"\bwould reformat\b", re.I), 1.0),
        (re.compile(r"Oh no!.*reformat", re.I | re.S), 0.9),
        (re.compile(r"isort[^:]*:.*(unsorted|error)", re.I), 1.0),
        (re.compile(r"prettier.*(error|check)", re.I), 0.9),
        (re.compile(r"ruff format.*check|ruff.*--check.*format", re.I), 0.9),
        (re.compile(r"black.*would reformat|autoflake.*error", re.I), 0.9),
    ],
    ErrorType.LINT: [
        (re.compile(r"\b[EWF]\d{3,4}\b"), 1.0),
        (re.compile(r"ruff check.*error|Found \d+ error", re.I), 0.8),
        (re.compile(r"\bpylint\b.*error|error.*\bpylint\b", re.I), 0.9),
        (re.compile(r"\beslint\b", re.I), 0.9),
        (re.compile(r":\d+:\d+: [EW]\d+ "), 0.9),
    ],
    ErrorType.SIMPLE_TYPE: [
        (re.compile(r"error: Argument \d+ .* incompatible type"), 1.0),
        (re.compile(r"error: Incompatible types in assignment"), 1.0),
        (re.compile(r"error: Item .* of .* has no attribute"), 0.9),
        (re.compile(r"is not assignable to (type|parameter)", re.I), 1.0),
        (re.compile(r"mypy.*: error|pyright.*: error", re.I), 0.6),
    ],
    ErrorType.CONFIG_CI: [
        (re.compile(r"\.gitlab-ci\.yml"), 1.0),
        (re.compile(r"\.github/workflows/.*\.ya?ml"), 1.0),
        (re.compile(r"ci(\/cd)? configuration.*(error|invalid|missing)", re.I), 0.9),
        (re.compile(r"yaml.*(syntax error|is invalid)|syntax error.*yaml", re.I), 0.9),
    ],
    ErrorType.BUILD: [
        (re.compile(r"/bin/(?:sh|bash):.*not found", re.I), 1.0),
        (re.compile(r"\bcommand not found\b", re.I), 0.9),
        (re.compile(r"\b(?:apt-get|apt|apk|yum|dnf|brew)\b.*(?:error|not found|failed)", re.I), 1.0),
        (re.compile(r"\b(?:npm|yarn|bun|pip|pip3)\b.*(?:error|failed|not found)", re.I), 0.9),
        (re.compile(r"curl:\s*\(\d+\)\s", re.I), 0.9),
        (re.compile(r"\bCOPY failed\b|\bRUN returned non-zero exit code\b", re.I), 1.0),
        (re.compile(r"\bdocker\b.*(?:error|failed|denied)", re.I), 0.8),
        (re.compile(r"error: unrecognized arguments?:", re.I), 1.0),
        (re.compile(r"(?:pytest|python|node|npm|go): error:", re.I), 0.9),
    ],
    ErrorType.TEST_CONTRACT: [
        (re.compile(r"FAILED\s+\S+\.py::"), 1.0),
        (re.compile(r"\bAssertionError\b"), 1.0),
        (re.compile(r"\d+ failed(?:, \d+ passed)?"), 0.9),
        (re.compile(r"pytest.*\d+ error", re.I), 0.7),
    ],
    ErrorType.LOGIC_ERROR: [
        (re.compile(r"^Traceback \(most recent call last\)", re.M), 1.0),
        (re.compile(r"\b(AttributeError|NameError|TypeError|IndexError|KeyError): "), 1.0),
        (re.compile(r"\b(ModuleNotFoundError|ImportError): "), 1.0),
        (re.compile(r"\b(RuntimeError|ValueError|OverflowError|ZeroDivisionError): "), 0.9),
    ],
}

_FILE_REF_RE = re.compile(
    r"(?:^|[\s(])"
    r"((?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|js|jsx|go|ya?ml|json|toml|cfg|ini|rb|java))"
    r"(?::\d+(?::\d+)?)?",
    re.M,
)
_STANDALONE_FILE_RE = re.compile(
    r"(?:^|[\s(])"
    r"([\w.-]+\.(?:py|ts|tsx|js|jsx|go|ya?ml|json|toml))"
    r"(?::\d+)?(?:$|[\s:])",
    re.M,
)
_TRACEBACK_FILE_RE = re.compile(
    r'File "(?:/[^"]*?/)?'
    r"((?:src|lib|app|scrapers|tests|pkg|internal|cmd)/[^\"]+\.py)"
    r'", line \d+',
    re.M,
)
_MODULE_NOT_FOUND_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):.*['\"](\w+(?:\.\w+)*)['\"]",
    re.M,
)
_PYTEST_COLLECT_RE = re.compile(
    r"ERROR\s+collecting\s+([\w/.-]+\.py)",
    re.M,
)


def _module_to_paths(module: str) -> list[str]:
    parts = module.split(".")
    base = "/".join(parts)
    return [f"{base}.py", f"{base}/__init__.py", f"src/{base}.py", f"src/{base}/__init__.py"]


def _regex_fallback(job_log: str) -> ClassificationResult:
    """Regex fallback when LLM is unavailable."""
    from collections import defaultdict

    scores: dict[ErrorType, float] = defaultdict(float)
    affected_files: set[str] = set()

    for line in job_log.splitlines():
        for m in _FILE_REF_RE.finditer(line):
            affected_files.add(m.group(1).strip())
        for m in _STANDALONE_FILE_RE.finditer(line):
            affected_files.add(m.group(1).strip())
        for error_type, rules in _FALLBACK_RULES.items():
            for pattern, weight in rules:
                if not (pattern.flags & re.S) and pattern.search(line):
                    scores[error_type] += weight

    # Multi-line patterns
    for error_type, rules in _FALLBACK_RULES.items():
        for pattern, weight in rules:
            if pattern.flags & re.S and pattern.search(job_log):
                scores[error_type] += weight * 0.3

    # Extract files from tracebacks, collection errors, module-not-found
    for m in _TRACEBACK_FILE_RE.finditer(job_log):
        affected_files.add(m.group(1))
    for m in _PYTEST_COLLECT_RE.finditer(job_log):
        affected_files.add(m.group(1))
    for m in _MODULE_NOT_FOUND_RE.finditer(job_log):
        for path in _module_to_paths(m.group(1)):
            affected_files.add(path)

    if any(kw in job_log for kw in ("ModuleNotFoundError", "ImportError", "pip install", "No module named")):
        affected_files.update(["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"])

    if not scores:
        return ClassificationResult(
            error_type=ErrorType.UNKNOWN,
            confidence=0.5,
            summary="No recognizable error patterns found in job log",
            affected_files=sorted(affected_files)[:20],
        )

    best_type = max(scores, key=lambda t: scores[t])
    total = sum(scores.values())
    confidence = min((scores[best_type] / total) * 1.05, 0.99) if total > 0 else 0.5

    # Extract summary
    error_re = re.compile(r"\b(error|fail|warning|FAIL|ERROR)\b", re.I)
    error_lines = [ln.strip() for ln in job_log.splitlines() if error_re.search(ln)][:5]
    if error_lines:
        snippet = "; ".join(ln[:120] for ln in error_lines[:3])
        summary = f"[{best_type.value}] {snippet}"
    else:
        summary = f"[{best_type.value}] CI job failed"

    return ClassificationResult(
        error_type=best_type,
        confidence=round(confidence, 2),
        summary=summary,
        affected_files=sorted(affected_files)[:20],
    )
