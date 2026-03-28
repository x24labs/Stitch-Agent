"""Tests for patch_validator module."""

from typing import Literal

from stitch_agent.core.fixer import FileChange, FixPatch
from stitch_agent.core.patch_validator import PatchValidator
from stitch_agent.models import ValidationConfig


def _make_patch(path: str, content: str, action: Literal["update", "create", "delete"] = "update") -> FixPatch:
    return FixPatch(
        changes=[FileChange(path=path, new_content=content, action=action)],
        commit_message="fix(test): test",
        explanation="test",
    )


# --- Diff ratio ---

ORIGINAL_PY = """\
import os

def greet(name: str) -> str:
    return f"Hello, {name}"

def farewell(name: str) -> str:
    return f"Goodbye, {name}"
"""


def test_minimal_change_passes():
    """A one-line fix should pass all checks."""
    new = ORIGINAL_PY.replace('"Hello, {name}"', '"Hi, {name}"')
    patch = _make_patch("app.py", new)
    result = PatchValidator().validate(patch, {"app.py": ORIGINAL_PY})
    assert result.passed


def test_full_rewrite_fails():
    """Rewriting the entire file should trigger diff_ratio violation."""
    rewritten = """\
from pathlib import Path

class Greeter:
    def __init__(self, prefix: str = "Hey"):
        self.prefix = prefix

    def greet(self, name: str) -> str:
        return f"{self.prefix}, {name}"
"""
    patch = _make_patch("app.py", rewritten)
    result = PatchValidator().validate(patch, {"app.py": ORIGINAL_PY})
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "diff_ratio" in checks


# --- Export removal ---

ORIGINAL_TS = """\
export const defaultLang = 'en';

export function getLangUrl(lang: string, path = '/'): string {
  if (lang === defaultLang) return path;
  return `/${lang}${path}`;
}

export function isRtl(lang: string): boolean {
  return rtlLanguages.has(lang);
}
"""


def test_export_removal_detected():
    """Removing an exported function should be flagged."""
    new = """\
export const defaultLang = 'en';

export function getLangUrl(lang: string, path = '/'): string {
  if (lang === defaultLang) return path;
  return `/${lang}${path}`;
}
"""
    patch = _make_patch("config.ts", new)
    result = PatchValidator().validate(patch, {"config.ts": ORIGINAL_TS})
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "export_removed" in checks
    assert any("isRtl" in v.detail for v in result.violations)


def test_adding_export_is_fine():
    """Adding a new export should not be flagged by export_removed."""
    new = ORIGINAL_TS + "\nexport function newHelper(): void {}\n"
    patch = _make_patch("config.ts", new)
    cfg = ValidationConfig(max_lines_changed=300)
    result = PatchValidator(cfg).validate(patch, {"config.ts": ORIGINAL_TS})
    checks = {v.check for v in result.violations}
    assert "export_removed" not in checks


# --- Signature change ---


def test_signature_change_detected_ts():
    """Changing function params should be flagged."""
    new = ORIGINAL_TS.replace(
        "export function getLangUrl(lang: string, path = '/'): string {",
        "export function getLangUrl(lang: string): string {",
    )
    patch = _make_patch("config.ts", new)
    result = PatchValidator().validate(patch, {"config.ts": ORIGINAL_TS})
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "signature_changed" in checks


def test_signature_change_detected_py():
    """Changing Python function params should be flagged."""
    new = ORIGINAL_PY.replace(
        "def greet(name: str) -> str:",
        "def greet(name: str, formal: bool = False) -> str:",
    )
    patch = _make_patch("app.py", new)
    result = PatchValidator().validate(patch, {"app.py": ORIGINAL_PY})
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "signature_changed" in checks


def test_same_signature_passes():
    """Changing body but keeping signature should pass."""
    new = ORIGINAL_PY.replace(
        'return f"Hello, {name}"',
        'return f"Hello there, {name}"',
    )
    patch = _make_patch("app.py", new)
    result = PatchValidator().validate(patch, {"app.py": ORIGINAL_PY})
    checks = {v.check for v in result.violations}
    assert "signature_changed" not in checks


# --- New imports ---


def test_new_import_detected():
    """Adding a new import should be flagged."""
    new = "import requests\n" + ORIGINAL_PY
    patch = _make_patch("app.py", new)
    result = PatchValidator().validate(patch, {"app.py": ORIGINAL_PY})
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "new_import" in checks


def test_existing_import_not_flagged():
    """Keeping existing imports should not be flagged."""
    new = ORIGINAL_PY.replace('"Hello, {name}"', '"Hi, {name}"')
    patch = _make_patch("app.py", new)
    result = PatchValidator().validate(patch, {"app.py": ORIGINAL_PY})
    checks = {v.check for v in result.violations}
    assert "new_import" not in checks


# --- File count ---


def test_max_files_exceeded():
    """Changing too many files should be flagged."""
    changes = [
        FileChange(path=f"file{i}.py", new_content="x", action="update")
        for i in range(6)
    ]
    patch = FixPatch(changes=changes, commit_message="fix: many", explanation="")
    originals = {f"file{i}.py": "x" for i in range(6)}
    result = PatchValidator().validate(patch, originals)
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "max_files_changed" in checks


# --- Delete protection ---


def test_delete_rejected():
    """Deleting a file should always be rejected."""
    patch = _make_patch("important.py", "", action="delete")
    result = PatchValidator().validate(patch, {})
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "delete_protection" in checks


# --- Config disabled ---


def test_disabled_passes_everything():
    """When validation is disabled, all patches pass."""
    rewritten = "completely different content"
    patch = _make_patch("app.py", rewritten)
    cfg = ValidationConfig(enabled=False)
    result = PatchValidator(cfg).validate(patch, {"app.py": ORIGINAL_PY})
    assert result.passed


# --- Missing original (fail closed) ---


def test_missing_original_fails_closed():
    """Updates without original content should be rejected."""
    patch = _make_patch("unknown.py", "some content")
    result = PatchValidator().validate(patch, {})
    assert not result.passed
    checks = {v.check for v in result.violations}
    assert "missing_original" in checks


# --- Unknown language only gets agnostic checks ---


def test_unknown_lang_only_agnostic_checks():
    """Files with unknown extensions should only get diff/line checks."""
    # Use enough lines so a 1-line change stays under the diff ratio threshold
    lines = [f"fn helper_{i}() -> i32 {{ {i} }}" for i in range(20)]
    original = "\n".join(lines) + "\n"
    new = original.replace("fn helper_0() -> i32 { 0 }", "fn helper_0() -> i32 { 42 }")
    patch = _make_patch("main.rs", new)
    result = PatchValidator().validate(patch, {"main.rs": original})
    assert result.passed


# --- Reproduce the actual bug: LLM rewrites config.ts ---


def test_real_world_config_ts_rewrite_rejected():
    """The actual bug: LLM rewrites config.ts removing getLangUrl's path param."""
    original = """\
export const defaultLang = 'en';

export const languages: Record<string, { label: string; flag: string }> = {
  en: { label: 'English', flag: 'EN' },
  zh: { label: '中文', flag: 'ZH' },
  es: { label: 'Español', flag: 'ES' },
  ar: { label: 'العربية', flag: 'AR' },
};

export const rtlLanguages = new Set(['ar', 'he']);

export const locales = Object.keys(languages);

export function isRtl(lang: string): boolean {
  return rtlLanguages.has(lang) + 1;
}

export function getDir(lang: string): 'ltr' | 'rtl' {
  return isRtl(lang) ? 'rtl' : 'ltr';
}

export function getLangUrl(lang: string, path = '/'): string {
  if (lang === defaultLang) return path;
  return `/${lang}${path}`;
}

export function t(key: string, lang: string = defaultLang): string {
  return translations[lang]?.[key] ?? translations[defaultLang]?.[key] ?? key;
}
"""
    llm_rewrite = """\
import { t as i18nT, type TFunction } from 'i18n-js';

const rtlLanguages = new Set(['ar', 'he', 'fa', 'ur']);

const languages = {
  en: 'English',
  es: 'Español',
};

const defaultLang = 'en';

function getDir(lang: string): 'ltr' | 'rtl' {
  return rtlLanguages.has(lang) ? 'rtl' : 'ltr';
}

function getDirMultiplier(lang: string): number {
  return (rtlLanguages.has(lang) ? 1 : 0) + 1;
}

function getLangUrl(lang: string): string {
  return lang === defaultLang ? '/' : `/${lang}/`;
}

function t(key: string, lang: string = defaultLang): string {
  return i18nT(key, { locale: lang });
}

export { languages, defaultLang, getDir, getDirMultiplier, getLangUrl, t };
"""
    patch = _make_patch("src/i18n/config.ts", llm_rewrite)
    result = PatchValidator().validate(
        patch, {"src/i18n/config.ts": original}
    )
    assert not result.passed
    checks = {v.check for v in result.violations}
    # Should catch multiple violations
    assert "diff_ratio" in checks  # Full rewrite
    assert "export_removed" in checks  # isRtl, locales removed


# --- Relaxed mode for test/build errors ---

def test_relaxed_mode_allows_new_imports() -> None:
    from stitch_agent.models import ErrorType

    original = "def foo():\n    return 1\n"
    new_content = "import os\n\ndef foo():\n    return os.getenv('X', '1')\n"
    patch = _make_patch("src/foo.py", new_content)
    config = ValidationConfig(block_new_imports=True)
    result = PatchValidator(config).validate(
        patch, {"src/foo.py": original}, ErrorType.TEST_CONTRACT
    )
    # Relaxed mode should skip import check
    assert not any(v.check == "new_import" for v in result.violations)


def test_relaxed_mode_allows_missing_original() -> None:
    from stitch_agent.models import ErrorType

    patch = _make_patch("pyproject.toml", "[project]\nname='x'\n")
    result = PatchValidator().validate(
        patch, {}, ErrorType.BUILD
    )
    # Relaxed mode should not fail on missing original
    assert not any(v.check == "missing_original" for v in result.violations)


def test_strict_mode_blocks_new_imports() -> None:
    from stitch_agent.models import ErrorType

    original = "def foo():\n    return 1\n"
    new_content = "import os\n\ndef foo():\n    return os.getenv('X', '1')\n"
    patch = _make_patch("src/foo.py", new_content)
    config = ValidationConfig(block_new_imports=True)
    result = PatchValidator(config).validate(
        patch, {"src/foo.py": original}, ErrorType.LINT
    )
    assert any(v.check == "new_import" for v in result.violations)
