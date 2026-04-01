"""Programmatic validation of LLM-generated patches.

Rejects destructive fixes (full rewrites, signature changes, export removal)
before they get pushed to a fix branch.
"""

from __future__ import annotations

import ast
import difflib
import os
import re
from typing import TYPE_CHECKING

from stitch_agent.models import ErrorType, PatchViolation, ValidationConfig, ValidationResult

if TYPE_CHECKING:
    from stitch_agent.core.fixer import FileChange, FixPatch

# Error types where we allow new imports and relax signature checks
_RELAXED_TYPES: frozenset[ErrorType] = frozenset({
    ErrorType.TEST_CONTRACT,
    ErrorType.LOGIC_ERROR,
    ErrorType.BUILD,
    ErrorType.COMPLEX_TYPE,
    ErrorType.UNKNOWN,
})

_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
}

# Export patterns per language
_EXPORT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^(?:async\s+)?def\s+(\w+)\s*\(", re.M),
        re.compile(r"^class\s+(\w+)[\s(:]", re.M),
    ],
    "typescript": [
        re.compile(r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.M),
        re.compile(r"export\s+(?:const|let|var)\s+(\w+)", re.M),
        re.compile(r"export\s+(?:class|interface|type|enum)\s+(\w+)", re.M),
    ],
    "javascript": [
        re.compile(r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.M),
        re.compile(r"export\s+(?:const|let|var)\s+(\w+)", re.M),
        re.compile(r"export\s+(?:class)\s+(\w+)", re.M),
    ],
}

# Signature patterns per language (name + params)
_SIG_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(
        r"^[ \t]*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)", re.M
    ),
    "typescript": re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.M
    ),
    "javascript": re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.M
    ),
}

# Import patterns per language
_IMPORT_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^(?:import\s+\S+|from\s+\S+\s+import\s+)", re.M),
    "typescript": re.compile(r"^import\s+.+\s+from\s+['\"]", re.M),
    "javascript": re.compile(r"^import\s+.+\s+from\s+['\"]", re.M),
}


def _detect_lang(file_path: str) -> str | None:
    ext = os.path.splitext(file_path)[1].lower()
    return _LANG_MAP.get(ext)


def _normalize(text: str) -> list[str]:
    """Normalize text for comparison: strip trailing whitespace per line."""
    return [line.rstrip() for line in text.splitlines()]


class PatchValidator:
    def __init__(self, config: ValidationConfig | None = None) -> None:
        self.config = config or ValidationConfig()

    def validate(
        self,
        patch: FixPatch,
        original_contents: dict[str, str],
        error_type: ErrorType | None = None,
    ) -> ValidationResult:
        if not self.config.enabled:
            return ValidationResult(passed=True)

        relaxed = error_type is not None and error_type in _RELAXED_TYPES
        violations: list[PatchViolation] = []

        # Global: file count check
        if len(patch.changes) > self.config.max_files_changed:
            violations.append(
                PatchViolation(
                    file_path="(global)",
                    check="max_files_changed",
                    detail=(
                        f"Patch changes {len(patch.changes)} files, "
                        f"max allowed is {self.config.max_files_changed}"
                    ),
                )
            )

        for change in patch.changes:
            # Delete protection
            if change.action == "delete":
                violations.append(
                    PatchViolation(
                        file_path=change.path,
                        check="delete_protection",
                        detail="stitch-agent must not delete files",
                    )
                )
                continue

            original = original_contents.get(change.path)
            if original is None and change.action == "update":
                if relaxed:
                    # For test/build errors, allow changes to files we don't have
                    # originals for (e.g., config files, new source files)
                    continue
                violations.append(
                    PatchViolation(
                        file_path=change.path,
                        check="missing_original",
                        detail="Cannot validate: original file content unavailable",
                    )
                )
                continue

            if original is not None:
                violations.extend(
                    self._check_file(change, original, relaxed=relaxed)
                )

        passed = not any(v.severity == "error" for v in violations)
        return ValidationResult(passed=passed, violations=violations)

    def _check_file(
        self, change: FileChange, original: str, *, relaxed: bool = False,
    ) -> list[PatchViolation]:
        violations: list[PatchViolation] = []
        lang = _detect_lang(change.path)

        # 1. Diff ratio — primary safety net (always enforced, but relaxed threshold)
        orig_lines = _normalize(original)
        new_lines = _normalize(change.new_content)
        ratio = difflib.SequenceMatcher(None, orig_lines, new_lines).ratio()
        diff_ratio = 1.0 - ratio

        max_ratio = 0.60 if relaxed else self.config.max_diff_ratio
        if diff_ratio > max_ratio:
            violations.append(
                PatchViolation(
                    file_path=change.path,
                    check="diff_ratio",
                    detail=(
                        f"File changed by {diff_ratio:.0%} "
                        f"(max {max_ratio:.0%}). "
                        f"This looks like a full rewrite, not a minimal fix."
                    ),
                )
            )

        # 2. Line count delta (relaxed: allow more changes)
        diff_lines = list(
            difflib.unified_diff(orig_lines, new_lines, lineterm="")
        )
        changed_lines = sum(
            1 for line in diff_lines if line.startswith(("+", "-"))
            and not line.startswith(("+++", "---"))
        )
        max_lines = self.config.max_lines_changed * 2 if relaxed else self.config.max_lines_changed
        if changed_lines > max_lines:
            violations.append(
                PatchViolation(
                    file_path=change.path,
                    check="max_lines_changed",
                    detail=(
                        f"{changed_lines} lines changed, "
                        f"max allowed is {max_lines}"
                    ),
                )
            )

        # 3. Syntax validation for Python files
        if lang == "python":
            try:
                ast.parse(change.new_content, filename=change.path)
            except SyntaxError as exc:
                violations.append(
                    PatchViolation(
                        file_path=change.path,
                        check="syntax_error",
                        detail=f"Generated code has invalid syntax: {exc}",
                    )
                )

        if lang is None:
            return violations

        # 4. Export removal detection (always enforced)
        if self.config.block_export_removal:
            violations.extend(
                self._check_exports(change.path, original, change.new_content, lang)
            )

        # 4. Signature preservation (skip in relaxed mode)
        if self.config.block_signature_changes and not relaxed:
            violations.extend(
                self._check_signatures(change.path, original, change.new_content, lang)
            )

        # 5. New import detection (skip in relaxed mode)
        if self.config.block_new_imports and not relaxed:
            violations.extend(
                self._check_imports(change.path, original, change.new_content, lang)
            )

        return violations

    def _check_exports(
        self, path: str, original: str, new_content: str, lang: str
    ) -> list[PatchViolation]:
        patterns = _EXPORT_PATTERNS.get(lang, [])
        if not patterns:
            return []

        original_exports: set[str] = set()
        for pat in patterns:
            original_exports.update(m.group(1) for m in pat.finditer(original))

        new_exports: set[str] = set()
        for pat in patterns:
            new_exports.update(m.group(1) for m in pat.finditer(new_content))

        removed = original_exports - new_exports
        if removed:
            return [
                PatchViolation(
                    file_path=path,
                    check="export_removed",
                    detail=f"Removed exports: {', '.join(sorted(removed))}",
                )
            ]
        return []

    def _check_signatures(
        self, path: str, original: str, new_content: str, lang: str
    ) -> list[PatchViolation]:
        pat = _SIG_PATTERNS.get(lang)
        if pat is None:
            return []

        orig_sigs: dict[str, str] = {}
        for m in pat.finditer(original):
            name = m.group(1)
            params = re.sub(r"\s+", " ", m.group(2).strip())
            orig_sigs[name] = params

        new_sigs: dict[str, str] = {}
        for m in pat.finditer(new_content):
            name = m.group(1)
            params = re.sub(r"\s+", " ", m.group(2).strip())
            new_sigs[name] = params

        violations: list[PatchViolation] = []
        for name, orig_params in orig_sigs.items():
            new_params = new_sigs.get(name)
            if new_params is None:
                continue  # Function removed — caught by export_removed check
            if orig_params != new_params:
                violations.append(
                    PatchViolation(
                        file_path=path,
                        check="signature_changed",
                        detail=(
                            f"Function '{name}' signature changed: "
                            f"({orig_params}) → ({new_params})"
                        ),
                    )
                )
        return violations

    def _check_imports(
        self, path: str, original: str, new_content: str, lang: str
    ) -> list[PatchViolation]:
        pat = _IMPORT_PATTERNS.get(lang)
        if pat is None:
            return []

        orig_imports = set(pat.findall(original))
        new_imports = set(pat.findall(new_content))
        added = new_imports - orig_imports

        if added:
            return [
                PatchViolation(
                    file_path=path,
                    check="new_import",
                    detail=f"New imports added: {', '.join(sorted(added))}",
                )
            ]
        return []
