"""Tests for stitch_agent.run.repo_context."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stitch_agent.run.repo_context import analyze_repo

if TYPE_CHECKING:
    from pathlib import Path


def test_detect_python_with_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "foo"\n[tool.pytest]\n[tool.ruff]\n'
    )
    ctx = analyze_repo(tmp_path)
    assert "python" in ctx.languages
    assert "pytest" in ctx.frameworks
    assert "ruff" in ctx.frameworks


def test_detect_javascript_with_bun(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "app",
        "devDependencies": {"vitest": "^1.0", "@biomejs/biome": "^1.0"},
        "scripts": {"test": "vitest", "lint": "biome check ."},
    }))
    (tmp_path / "bun.lockb").write_bytes(b"")
    ctx = analyze_repo(tmp_path)
    assert "javascript" in ctx.languages
    assert ctx.package_manager == "bun"
    assert "vitest" in ctx.frameworks
    assert "biome" in ctx.frameworks


def test_detect_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/foo\n\ngo 1.22\n")
    ctx = analyze_repo(tmp_path)
    assert "go" in ctx.languages
    assert ctx.package_manager == "go"


def test_detect_rust(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"\n')
    ctx = analyze_repo(tmp_path)
    assert "rust" in ctx.languages
    assert ctx.package_manager == "cargo"


def test_detect_gitlab_ci(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text(
        "test:\n  script: pytest\nlint:\n  script: ruff check .\n"
    )
    ctx = analyze_repo(tmp_path)
    assert ctx.ci_platform == "gitlab"
    assert ctx.existing_ci_file == ".gitlab-ci.yml"
    assert ctx.has_test_jobs is True


def test_detect_github_ci(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: ci\non: push\njobs:\n  test:\n    steps:\n      - run: npm test\n"
    )
    ctx = analyze_repo(tmp_path)
    assert ctx.ci_platform == "github"
    assert ctx.has_test_jobs is True


def test_no_test_jobs_detected(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text(
        "deploy:\n  script: echo deploy\n"
    )
    ctx = analyze_repo(tmp_path)
    assert ctx.ci_platform == "gitlab"
    assert ctx.has_test_jobs is False


def test_no_ci_config(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')\n")
    ctx = analyze_repo(tmp_path)
    assert ctx.ci_platform is None
    assert ctx.has_test_jobs is False


def test_summary_format(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\n')
    (tmp_path / ".gitlab-ci.yml").write_text("test:\n  script: pytest\n")
    ctx = analyze_repo(tmp_path)
    s = ctx.summary()
    assert "python" in s.lower()
    assert "gitlab" in s.lower()


def test_entry_files_collected(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    ctx = analyze_repo(tmp_path)
    assert "Makefile" in ctx.entry_files
    assert "Dockerfile" in ctx.entry_files
