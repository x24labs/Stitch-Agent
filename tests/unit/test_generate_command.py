"""Tests for runners.generate_command."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from runners.generate_command import _build_prompt, run_generate_command

if TYPE_CHECKING:
    from pathlib import Path


def test_build_prompt_no_ci(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\n')
    prompt, summary = _build_prompt(tmp_path)
    assert "python" in summary.lower()
    assert "no CI configuration" in prompt.lower() or "generate" in prompt.lower()


def test_build_prompt_with_existing_ci(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text("deploy:\n  script: echo deploy\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\n')
    prompt, summary = _build_prompt(tmp_path)
    assert "existing CI" in prompt or "Existing CI" in prompt
    assert "deploy" in prompt


class _FakeArgs(argparse.Namespace):
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(
            agent=kwargs.get("agent", "claude"),
            repo=kwargs.get("repo", "."),
            output=kwargs.get("output", "text"),
            dry_run=kwargs.get("dry_run", False),
        )


@pytest.mark.asyncio
async def test_dry_run_returns_zero(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\n')
    args = _FakeArgs(repo=str(tmp_path), dry_run=True)
    result = await run_generate_command(args)
    assert result == 0


@pytest.mark.asyncio
async def test_dry_run_json_output(tmp_path: Path, capsys) -> None:
    (tmp_path / "go.mod").write_text("module example.com/foo\n\ngo 1.22\n")
    args = _FakeArgs(repo=str(tmp_path), dry_run=True, output="json")
    result = await run_generate_command(args)
    assert result == 0
    import json
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "go" in data["languages"]


@pytest.mark.asyncio
async def test_invalid_repo_returns_2() -> None:
    args = _FakeArgs(repo="/nonexistent/path/xyz")
    result = await run_generate_command(args)
    assert result == 2


@pytest.mark.asyncio
async def test_calls_claude_agent(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\n')
    args = _FakeArgs(repo=str(tmp_path), agent="claude")

    with patch(
        "runners.generate_command._call_claude",
        new_callable=AsyncMock,
        return_value="stages:\n  - test\n\ntest:\n  script: pytest\n",
    ) as mock_call:
        result = await run_generate_command(args)
        assert result == 0
        mock_call.assert_called_once()


@pytest.mark.asyncio
async def test_calls_codex_agent(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "app"}')
    args = _FakeArgs(repo=str(tmp_path), agent="codex")

    with patch(
        "runners.generate_command._call_codex",
        new_callable=AsyncMock,
        return_value="name: ci\non: push\njobs:\n  test:\n    steps:\n      - run: npm test\n",
    ) as mock_call:
        result = await run_generate_command(args)
        assert result == 0
        mock_call.assert_called_once()


@pytest.mark.asyncio
async def test_agent_failure_returns_1(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest]\n')
    args = _FakeArgs(repo=str(tmp_path), agent="claude")

    with patch(
        "runners.generate_command._call_claude",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await run_generate_command(args)
        assert result == 1
