from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from stitch_agent.core.agent import StitchAgent
from stitch_agent.core.fixer import FileChange, FixPatch
from stitch_agent.models import ErrorType, FixRequest, FixResult


def _make_request(**kwargs: str) -> FixRequest:
    defaults = dict(
        platform="gitlab",
        project_id="42",
        pipeline_id="100",
        job_id="200",
        branch="feature/test",
    )
    defaults.update(kwargs)
    return FixRequest(**defaults)  # type: ignore[arg-type]


def _make_adapter(
    *,
    prev_count: int = 0,
    job_log: str = "src/foo.py:1: F401 unused import",
    diff: str = "--- a/src/foo.py\n+++ b/src/foo.py",
    repo_config: str | None = None,
) -> AsyncMock:
    adapter = AsyncMock()
    adapter.get_previous_fix_count = AsyncMock(return_value=prev_count)
    adapter.fetch_job_logs = AsyncMock(return_value=job_log)
    adapter.fetch_diff = AsyncMock(return_value=diff)
    adapter.fetch_file_content = AsyncMock(return_value="import os\n\nx = 1\n")
    adapter.get_repo_config = AsyncMock(return_value=repo_config)
    adapter.create_fix_branch = AsyncMock(return_value="stitch/fix-100")
    adapter.create_merge_request = AsyncMock(return_value="https://gitlab.com/p/mr/1")
    return adapter


def _make_agent(adapter: AsyncMock, *, max_attempts: int = 3) -> StitchAgent:
    agent = StitchAgent(
        adapter=adapter,
        anthropic_api_key="test-key",
        max_attempts=max_attempts,
    )
    return agent


pytestmark = pytest.mark.asyncio


async def test_escalates_when_max_attempts_reached() -> None:
    adapter = _make_adapter(prev_count=3)
    agent = _make_agent(adapter, max_attempts=3)
    result = await agent.fix(_make_request())
    assert result.status == "escalate"
    assert "Max attempts" in result.reason


async def test_escalates_for_logic_error_type() -> None:
    adapter = _make_adapter(
        job_log="Traceback (most recent call last):\n  File 'app.py'\nValueError: bad input\n"
    )
    agent = _make_agent(adapter)
    result = await agent.fix(_make_request())
    assert result.status == "escalate"
    assert result.error_type == ErrorType.LOGIC_ERROR


async def test_escalates_when_confidence_below_threshold() -> None:
    adapter = _make_adapter(job_log="Build failed with unknown error\n")
    agent = _make_agent(adapter)
    agent.haiku_confidence_threshold = 1.0
    agent.sonnet_confidence_threshold = 1.0
    result = await agent.fix(_make_request())
    assert result.status == "escalate"


async def test_successful_fix_flow() -> None:
    adapter = _make_adapter(
        job_log="src/foo.py:1:1: F401 'os' imported but unused\nFound 1 error.\n",
    )
    agent = _make_agent(adapter)

    fix_patch = FixPatch(
        changes=[FileChange(path="src/foo.py", new_content="x = 1\n")],
        commit_message="fix(lint): remove unused import",
        explanation="Removed unused import of os.",
    )
    agent.fixer = AsyncMock()
    agent.fixer.generate_fix = AsyncMock(return_value=fix_patch)

    result = await agent.fix(_make_request())

    assert result.status == "fixed"
    assert result.mr_url == "https://gitlab.com/p/mr/1"
    assert result.fix_branch == "stitch/fix-100"
    adapter.create_fix_branch.assert_called_once()
    adapter.create_merge_request.assert_called_once()


async def test_returns_error_when_no_changes() -> None:
    adapter = _make_adapter(
        job_log="src/foo.py:1:1: F401 'os' imported but unused\nFound 1 error.\n",
    )
    agent = _make_agent(adapter)

    agent.fixer = AsyncMock()
    agent.fixer.generate_fix = AsyncMock(return_value=FixPatch(changes=[]))

    result = await agent.fix(_make_request())
    assert result.status == "error"
    assert "no file changes" in result.reason


async def test_loads_repo_config_if_present() -> None:
    adapter = _make_adapter(
        job_log="src/foo.py:1:1: F401 unused\nFound 1 error.\n",
        repo_config="languages: [python]\nlinter: ruff\n",
    )
    good_patch = FixPatch(
        changes=[FileChange(path="src/foo.py", new_content="x = 1\n")],
        commit_message="fix: x",
        explanation="e",
    )
    mock_fixer = AsyncMock()
    mock_fixer.generate_fix = AsyncMock(return_value=good_patch)

    with patch("stitch_agent.core.agent.Fixer", return_value=mock_fixer):
        agent = _make_agent(adapter)
        result = await agent.fix(_make_request())

    assert result.status == "fixed"
    assert agent.classifier.config.linter == "ruff"


async def test_fix_result_is_pydantic_model() -> None:
    adapter = _make_adapter(prev_count=5)
    agent = _make_agent(adapter, max_attempts=3)
    result = await agent.fix(_make_request())
    assert isinstance(result, FixResult)
    assert result.model_dump()["status"] == "escalate"
