"""Tests for stitch_agent.run.ci_detect."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stitch_agent.run.ci_detect import CIPlatform, detect_platform

if TYPE_CHECKING:
    from pathlib import Path


def test_detect_gitlab_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert detect_platform(tmp_path) == CIPlatform.GITLAB


def test_detect_github_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert detect_platform(tmp_path) == CIPlatform.GITHUB


def test_env_takes_precedence_over_files(monkeypatch, tmp_path: Path) -> None:
    """Even if only GitHub config exists, GITLAB_CI env wins."""
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    gh = tmp_path / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "ci.yml").write_text("name: ci\non: push\njobs: {}\n")
    assert detect_platform(tmp_path) == CIPlatform.GITLAB


def test_detect_gitlab_from_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    (tmp_path / ".gitlab-ci.yml").write_text("test:\n  script: echo hi\n")
    assert detect_platform(tmp_path) == CIPlatform.GITLAB


def test_detect_github_from_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    gh = tmp_path / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "ci.yml").write_text("name: ci\n")
    assert detect_platform(tmp_path) == CIPlatform.GITHUB


def test_detect_unknown_when_both_configs_exist(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    (tmp_path / ".gitlab-ci.yml").write_text("test:\n  script: echo hi\n")
    gh = tmp_path / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "ci.yml").write_text("name: ci\n")
    assert detect_platform(tmp_path) == CIPlatform.UNKNOWN


def test_detect_unknown_when_no_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert detect_platform(tmp_path) == CIPlatform.UNKNOWN


def test_detect_without_repo_root(monkeypatch) -> None:
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert detect_platform() == CIPlatform.UNKNOWN
