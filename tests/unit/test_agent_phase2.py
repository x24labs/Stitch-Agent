from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from stitch_agent.core.agent import StitchAgent
from stitch_agent.core.fixer import FileChange, FixPatch
from stitch_agent.models import ErrorType, FixRequest, FixResult

pytestmark = pytest.mark.asyncio


def _req(**kwargs: str) -> FixRequest:
    defaults = dict(
        platform="gitlab", project_id="42", pipeline_id="100", job_id="200", branch="main"
    )
    defaults.update(kwargs)
    return FixRequest(**defaults)  # type: ignore[arg-type]


def _mock_adapter(**kwargs: object) -> AsyncMock:
    a = AsyncMock()
    a.get_previous_fix_count = AsyncMock(return_value=kwargs.get("prev_count", 0))
    a.fetch_job_logs = AsyncMock(return_value=str(kwargs.get("job_log", "F401 unused\n")))
    a.fetch_diff = AsyncMock(return_value="--- a/f.py\n+++ b/f.py")
    a.fetch_file_content = AsyncMock(return_value="old\n")
    a.get_repo_config = AsyncMock(return_value=None)
    a.create_fix_branch = AsyncMock(return_value="stitch/fix-100")
    a.create_merge_request = AsyncMock(return_value="https://example.com/mr/1")
    return a


def _agent(
    adapter: AsyncMock,
    haiku_thresh: float = 0.80,
    sonnet_thresh: float = 0.40,
    max_attempts: int = 3,
) -> StitchAgent:
    return StitchAgent(
        adapter=adapter,
        anthropic_api_key="test-key",
        haiku_confidence_threshold=haiku_thresh,
        sonnet_confidence_threshold=sonnet_thresh,
        max_attempts=max_attempts,
    )


async def test_escalation_reason_code_max_attempts() -> None:
    adapter = _mock_adapter(prev_count=3)
    agent = _agent(adapter)
    result = await agent.fix(_req())
    assert result.escalation_reason_code == "max_attempts_reached"


async def test_logic_error_attempts_fix() -> None:
    adapter = _mock_adapter(
        job_log="Traceback (most recent call last):\n  File 'app.py'\nValueError: bad\n"
    )
    mock_patch = FixPatch(changes=[FileChange(path="app.py", new_content="fixed")])
    agent = _agent(adapter)
    with patch.object(agent.fixer, "generate_fix", new_callable=AsyncMock, return_value=mock_patch):
        result = await agent.fix(_req())
    assert result.status == "fixed"


async def test_escalation_reason_code_low_confidence() -> None:
    adapter = _mock_adapter(job_log="src/f.py:1: F401 unused import\n")
    agent = _agent(adapter, haiku_thresh=1.0)
    result = await agent.fix(_req())
    assert result.status == "escalate"
    assert result.escalation_reason_code == "low_confidence"


async def test_escalation_reason_code_no_changes() -> None:
    adapter = _mock_adapter(job_log="src/f.py:1: F401 unused\nFound 1 error.\n")
    agent = _agent(adapter)
    agent.fixer = AsyncMock()
    agent.fixer.generate_fix = AsyncMock(return_value=FixPatch(changes=[]))
    result = await agent.fix(_req())
    assert result.status == "error"
    assert result.escalation_reason_code == "no_changes"


async def test_threshold_haiku_type_uses_haiku_threshold() -> None:
    agent = _agent(_mock_adapter())
    from stitch_agent.models import ErrorType

    assert agent._get_threshold(ErrorType.LINT) == agent.haiku_confidence_threshold
    assert agent._get_threshold(ErrorType.FORMAT) == agent.haiku_confidence_threshold
    assert agent._get_threshold(ErrorType.SIMPLE_TYPE) == agent.haiku_confidence_threshold
    assert agent._get_threshold(ErrorType.CONFIG_CI) == agent.haiku_confidence_threshold


async def test_threshold_sonnet_type_uses_sonnet_threshold() -> None:
    agent = _agent(_mock_adapter())
    assert agent._get_threshold(ErrorType.COMPLEX_TYPE) == agent.sonnet_confidence_threshold
    assert agent._get_threshold(ErrorType.TEST_CONTRACT) == agent.sonnet_confidence_threshold


async def test_notify_called_on_escalation() -> None:
    adapter = _mock_adapter(prev_count=3)
    callback = AsyncMock()
    agent = StitchAgent(
        adapter=adapter,
        anthropic_api_key="test-key",
        escalation_callback=callback,
    )
    await agent.fix(_req())
    callback.assert_called_once()
    args = callback.call_args[0]
    assert isinstance(args[0], FixRequest)
    assert isinstance(args[1], FixResult)
    assert args[1].status == "escalate"


async def test_notify_not_called_on_success() -> None:
    adapter = _mock_adapter(job_log="src/f.py:1: F401 unused\nFound 1 error.\n")
    callback = AsyncMock()
    agent = StitchAgent(adapter=adapter, anthropic_api_key="test-key", escalation_callback=callback)
    patch_val = FixPatch(
        changes=[FileChange(path="src/f.py", new_content="x=1\n")],
        commit_message="fix: x",
        explanation="ok",
    )
    agent.fixer = AsyncMock()
    agent.fixer.generate_fix = AsyncMock(return_value=patch_val)
    result = await agent.fix(_req())
    assert result.status == "fixed"
    callback.assert_not_called()


async def test_sonnet_type_lower_threshold_passes() -> None:
    adapter = _mock_adapter(
        job_log=(
            "FAILED tests/test_api.py::test_create - AssertionError\n1 failed, 5 passed in 0.45s\n"
        )
    )
    agent = _agent(adapter, sonnet_thresh=0.40, haiku_thresh=0.80)
    patch_val = FixPatch(
        changes=[FileChange(path="tests/test_api.py", new_content="fixed\n")],
        commit_message="fix(test): update expectation",
        explanation="Updated expected response",
    )
    agent.fixer = AsyncMock()
    agent.fixer.generate_fix = AsyncMock(return_value=patch_val)
    result = await agent.fix(_req())
    assert result.status in ("fixed", "escalate")
