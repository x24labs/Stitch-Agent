"""Tests for stitch_agent.run.drivers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from stitch_agent.run.drivers import (
    ApiDriver,
    ClaudeCodeDriver,
    CodexDriver,
    build_prompt,
)
from stitch_agent.run.models import FixContext

if TYPE_CHECKING:
    from pathlib import Path


def _ctx(tmp_path: Path) -> FixContext:
    return FixContext(
        repo_root=tmp_path,
        job_name="lint",
        command="ruff check .",
        script=["ruff check ."],
        error_log="E501 line too long\n",
        attempt=1,
    )


def test_build_prompt_includes_essentials(tmp_path: Path) -> None:
    prompt = build_prompt(_ctx(tmp_path))
    assert "lint" in prompt
    assert "ruff check ." in prompt
    assert "E501" in prompt
    assert str(tmp_path) in prompt


@pytest.mark.asyncio
async def test_claude_driver_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    driver = ClaudeCodeDriver()
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is False
    assert "not found" in outcome.reason


@pytest.mark.asyncio
async def test_claude_driver_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stitch_agent.run.drivers.claude_code.shutil.which",
        lambda _name: "/usr/bin/claude",
    )

    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"all good\n", b""))

    async def fake_exec(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    monkeypatch.setattr(
        "stitch_agent.run.drivers.claude_code.asyncio.create_subprocess_exec",
        fake_exec,
    )
    driver = ClaudeCodeDriver()
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is True
    assert "completed" in outcome.reason


@pytest.mark.asyncio
async def test_claude_driver_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stitch_agent.run.drivers.claude_code.shutil.which",
        lambda _name: "/usr/bin/claude",
    )
    proc = MagicMock()
    proc.returncode = 2
    proc.communicate = AsyncMock(return_value=(b"boom\n", b""))

    async def fake_exec(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    monkeypatch.setattr(
        "stitch_agent.run.drivers.claude_code.asyncio.create_subprocess_exec",
        fake_exec,
    )
    driver = ClaudeCodeDriver()
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is False
    assert "exited 2" in outcome.reason


@pytest.mark.asyncio
async def test_claude_driver_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stitch_agent.run.drivers.claude_code.shutil.which",
        lambda _name: "/usr/bin/claude",
    )
    proc = MagicMock()
    proc.returncode = None

    async def hang() -> Any:
        await asyncio.sleep(10)
        return (b"", b"")

    proc.communicate = hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)

    async def fake_exec(*_args: Any, **_kwargs: Any) -> Any:
        return proc

    monkeypatch.setattr(
        "stitch_agent.run.drivers.claude_code.asyncio.create_subprocess_exec",
        fake_exec,
    )
    driver = ClaudeCodeDriver(timeout_seconds=0.2)
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is False
    assert "timeout" in outcome.reason


@pytest.mark.asyncio
async def test_codex_driver_missing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "stitch_agent.run.drivers.codex.shutil.which", lambda _name: None
    )
    driver = CodexDriver()
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is False
    assert "not found" in outcome.reason


@pytest.mark.asyncio
async def test_api_driver_missing_key(tmp_path: Path) -> None:
    driver = ApiDriver(api_key="")
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is False
    assert "API_KEY" in outcome.reason or "api" in outcome.reason.lower()


@pytest.mark.asyncio
async def test_api_driver_applies_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ApiDriver makes an LLM call, parses JSON, writes files to disk."""

    class _FakeChoice:
        class _Msg:
            content = '{"changes": [{"path": "src/hello.py", "content": "print(42)\\n"}], "explanation": "fixed it"}'

        message = _Msg()

    class _FakeResponse:
        choices = [_FakeChoice()]

    async def fake_create(*_a: Any, **_kw: Any) -> _FakeResponse:
        return _FakeResponse()

    monkeypatch.setattr(
        "stitch_agent.run.drivers.api.AsyncOpenAI",
        lambda **_kw: type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": fake_create})()})()})(),
    )

    driver = ApiDriver(api_key="sk-test")
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is True
    assert (tmp_path / "src" / "hello.py").read_text() == "print(42)\n"
    assert "fixed it" in outcome.reason


@pytest.mark.asyncio
async def test_api_driver_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeChoice:
        class _Msg:
            content = '{"changes": [], "explanation": "cannot fix this"}'

        message = _Msg()

    class _FakeResponse:
        choices = [_FakeChoice()]

    async def fake_create(*_a: Any, **_kw: Any) -> _FakeResponse:
        return _FakeResponse()

    monkeypatch.setattr(
        "stitch_agent.run.drivers.api.AsyncOpenAI",
        lambda **_kw: type("C", (), {"chat": type("Ch", (), {"completions": type("Co", (), {"create": fake_create})()})()})(),
    )

    driver = ApiDriver(api_key="sk-test")
    outcome = await driver.fix(_ctx(tmp_path))
    assert outcome.applied is False
    assert "cannot fix" in outcome.reason or "no changes" in outcome.reason.lower()
