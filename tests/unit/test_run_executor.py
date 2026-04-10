"""Tests for stitch_agent.run.executor."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stitch_agent.run.executor import LocalExecutor
from stitch_agent.run.models import CIJob

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_execute_success(tmp_path: Path) -> None:
    executor = LocalExecutor(tmp_path)
    job = CIJob(name="hi", stage="test", script=["echo hello", "echo world"])
    result = await executor.run_job(job)
    assert result.exit_code == 0
    assert "hello" in result.log
    assert "world" in result.log


@pytest.mark.asyncio
async def test_execute_stops_on_first_failure(tmp_path: Path) -> None:
    executor = LocalExecutor(tmp_path)
    job = CIJob(name="fail", stage="test", script=["false", "echo unreached"])
    result = await executor.run_job(job)
    assert result.exit_code != 0
    assert "unreached" not in result.log


@pytest.mark.asyncio
async def test_execute_timeout(tmp_path: Path) -> None:
    executor = LocalExecutor(tmp_path, timeout_seconds=0.5)
    job = CIJob(name="slow", stage="test", script=["sleep 5"])
    result = await executor.run_job(job)
    assert result.timed_out is True
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_execute_empty_script(tmp_path: Path) -> None:
    executor = LocalExecutor(tmp_path)
    job = CIJob(name="noop", stage="test", script=[])
    result = await executor.run_job(job)
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_execute_respects_cwd(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("here")
    executor = LocalExecutor(tmp_path)
    job = CIJob(name="ls", stage="test", script=["cat marker.txt"])
    result = await executor.run_job(job)
    assert result.exit_code == 0
    assert "here" in result.log
