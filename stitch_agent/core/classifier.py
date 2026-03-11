from __future__ import annotations

import re
from collections import defaultdict

from stitch_agent.models import ClassificationResult, ErrorType, StitchConfig

_RULES: dict[ErrorType, list[tuple[re.Pattern[str], float]]] = {
    ErrorType.FORMAT: [
        (re.compile(r"\bwould reformat\b", re.I), 1.0),
        (re.compile(r"Oh no!.*reformat", re.I | re.S), 0.9),
        (re.compile(r"isort[^:]*:.*(unsorted|error)", re.I), 1.0),
        (re.compile(r"import should (come before|come after|be at)", re.I), 0.8),
        (re.compile(r"prettier.*(error|check)", re.I), 0.9),
        (re.compile(r"ruff format.*check|ruff.*--check.*format", re.I), 0.9),
        (re.compile(r"black.*would reformat|autoflake.*error", re.I), 0.9),
        (re.compile(r"\b(E301|E302|E303|E304|E305|W291|W292|W293|W391)\b"), 0.8),
    ],
    ErrorType.LINT: [
        (re.compile(r"\b[EWF]\d{3,4}\b"), 1.0),
        (re.compile(r"ruff check.*error|Found \d+ error", re.I), 0.8),
        (re.compile(r"\bpylint\b.*error|error.*\bpylint\b", re.I), 0.9),
        (re.compile(r"\beslint\b", re.I), 0.9),
        (re.compile(r":\d+:\d+: [EW]\d+ "), 0.9),
        (re.compile(r"error: (cannot import|unused import|undefined name)", re.I), 0.8),
        (re.compile(r"\bno-unused-vars\b|\bno-undef\b"), 0.8),
    ],
    ErrorType.SIMPLE_TYPE: [
        (re.compile(r"error: Argument \d+ .* incompatible type"), 1.0),
        (re.compile(r"error: Incompatible types in assignment"), 1.0),
        (re.compile(r"error: Item .* of .* has no attribute"), 0.9),
        (re.compile(r"error: No overload variant .* matches argument"), 0.9),
        (re.compile(r"is not assignable to (type|parameter)", re.I), 1.0),
        (re.compile(r"Cannot find name '", re.I), 0.9),
        (re.compile(r"has no attribute '[\w]+'", re.I), 0.8),
        (re.compile(r"\bTS\d{4}\b"), 0.9),
        (re.compile(r"error\[E0\d{3}\]"), 0.8),
        (re.compile(r"mypy.*: error|pyright.*: error", re.I), 0.6),
    ],
    ErrorType.CONFIG_CI: [
        (re.compile(r"\.gitlab-ci\.yml"), 1.0),
        (re.compile(r"\.github/workflows/.*\.ya?ml"), 1.0),
        (re.compile(r"ci(\/cd)? configuration.*(error|invalid|missing)", re.I), 0.9),
        (re.compile(r"yaml.*(syntax error|is invalid)|syntax error.*yaml", re.I), 0.9),
        (re.compile(r"pipeline.*configuration.*(invalid|error)", re.I), 0.9),
        (re.compile(r"job .* (is not defined|doesn.t exist)", re.I), 0.9),
        (re.compile(r"unknown key(s)? in .* config", re.I), 0.8),
        (re.compile(r"stage .* is not defined", re.I), 0.8),
    ],
    ErrorType.COMPLEX_TYPE: [
        (re.compile(r"Cannot use .* as a (generic|base) type", re.I), 1.0),
        (re.compile(r"Protocol .* not satisfied|does not satisfy Protocol", re.I), 1.0),
        (re.compile(r"Overloaded .* implementation is not compatible", re.I), 0.9),
        (re.compile(r"TypeVar .* bound|covariant|contravariant", re.I), 0.8),
        (re.compile(r"Multiple overloads|overload.*ambiguous", re.I), 0.9),
        (re.compile(r"error: Unsupported left operand type", re.I), 0.8),
    ],
    ErrorType.TEST_CONTRACT: [
        (re.compile(r"FAILED\s+\S+\.py::"), 1.0),
        (re.compile(r"\bAssertionError\b"), 1.0),
        (re.compile(r"\d+ failed(?:, \d+ passed)?"), 0.9),
        (re.compile(r"^E\s+assert ", re.M), 0.9),
        (re.compile(r"Expected\s+.*\s+but\s+(got|was)\s+", re.I | re.S), 0.8),
        (re.compile(r"pytest.*\d+ error", re.I), 0.7),
    ],
    ErrorType.LOGIC_ERROR: [
        (re.compile(r"^Traceback \(most recent call last\)", re.M), 1.0),
        (re.compile(r"\b(AttributeError|NameError|TypeError|IndexError|KeyError): "), 1.0),
        (re.compile(r"\b(RuntimeError|ValueError|OverflowError|ZeroDivisionError): "), 0.9),
        (re.compile(r"SIGSEGV|Segmentation fault|core dumped", re.I), 0.9),
        (re.compile(r"Process finished with exit code [^0]"), 0.6),
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


class Classifier:
    def __init__(self, config: StitchConfig | None = None) -> None:
        self.config = config or StitchConfig()

    async def classify(self, job_log: str, diff: str | None = None) -> ClassificationResult:
        scores: dict[ErrorType, float] = defaultdict(float)
        affected_files: set[str] = set()
        lines = job_log.splitlines()

        for line in lines:
            for m in _FILE_REF_RE.finditer(line):
                affected_files.add(m.group(1).strip())
            for m in _STANDALONE_FILE_RE.finditer(line):
                affected_files.add(m.group(1).strip())

            for error_type, rules in _RULES.items():
                for pattern, weight in rules:
                    if not (pattern.flags & re.S) and pattern.search(line):
                        scores[error_type] += weight

        for error_type, rules in _RULES.items():
            for pattern, weight in rules:
                if pattern.flags & re.S and pattern.search(job_log):
                    scores[error_type] += weight * 0.3

        if not scores:
            return ClassificationResult(
                error_type=ErrorType.UNKNOWN,
                confidence=0.5,
                summary="No recognizable error patterns found in job log",
                affected_files=[],
            )

        best_type = max(scores, key=lambda t: scores[t])
        total_score = sum(scores.values())
        raw_confidence = scores[best_type] / total_score if total_score > 0 else 0.5
        confidence = min(raw_confidence * 1.05, 0.99)

        summary = _extract_summary(best_type, lines)
        return ClassificationResult(
            error_type=best_type,
            confidence=round(confidence, 2),
            summary=summary,
            affected_files=sorted(affected_files)[:20],
        )


def _extract_summary(error_type: ErrorType, lines: list[str]) -> str:
    error_re = re.compile(r"\b(error|fail|warning|FAIL|ERROR)\b", re.I)
    error_lines = [ln.strip() for ln in lines if error_re.search(ln)][:5]
    if error_lines:
        snippet = "; ".join(ln[:120] for ln in error_lines[:3])
        return f"[{error_type.value}] {snippet}"
    return f"[{error_type.value}] CI job failed"
