"""End-to-end CI runner state machine integration tests."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from runners.ci_runner import run_ci
from stitch_agent.models import ErrorType, FixResult

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fix_result(
    status: str = "fixed",
    error_type: ErrorType = ErrorType.LINT,
    fix_branch: str = "stitch/fix-100",
    reason: str = "Fixed lint error",
) -> FixResult:
    return FixResult(
        status=status,  # type: ignore[arg-type]
        error_type=error_type,
        confidence=0.92,
        reason=reason,
        fix_branch=fix_branch,
    )


def _make_mock_adapter(
    *,
    list_failed_jobs=None,
    count_branch_commits=0,
    create_merge_request_url="https://gitlab.example.com/p/mr/1",
    get_latest_commit_message="fix(lint): clean up\n\nStitch-Target: main",
    fetch_job_logs="job log content\nFAILED test_foo.py::test_bar\n",
) -> AsyncMock:
    adapter = AsyncMock()
    adapter.__aenter__ = AsyncMock(return_value=adapter)
    adapter.__aexit__ = AsyncMock(return_value=False)
    adapter.list_failed_jobs = AsyncMock(
        return_value=list_failed_jobs if list_failed_jobs is not None else []
    )
    adapter.count_branch_commits = AsyncMock(return_value=count_branch_commits)
    adapter.create_merge_request = AsyncMock(return_value=create_merge_request_url)
    adapter.get_latest_commit_message = AsyncMock(return_value=get_latest_commit_message)
    adapter.fetch_job_logs = AsyncMock(return_value=fetch_job_logs)
    return adapter


# ---------------------------------------------------------------------------
# Fix mode — no failures
# ---------------------------------------------------------------------------


async def test_fix_mode_no_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline has no failed jobs; exits cleanly with code 0."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    mock_adapter = _make_mock_adapter(list_failed_jobs=[])

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_adapter.list_failed_jobs.assert_called_once()


# ---------------------------------------------------------------------------
# Fix mode — fix applied
# ---------------------------------------------------------------------------


async def test_fix_mode_fix_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovers failed jobs, groups by error, agent fixes successfully."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    failed_jobs = [{"id": "200", "name": "lint", "status": "failed"}]
    mock_adapter = _make_mock_adapter(list_failed_jobs=failed_jobs)
    mock_result = _make_fix_result(status="fixed", fix_branch="stitch/fix-100")

    mock_agent = AsyncMock()
    mock_agent.fix = AsyncMock(return_value=mock_result)

    with (
        patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter),
        patch("runners.ci_runner.StitchAgent", return_value=mock_agent),
    ):
        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_agent.fix.assert_called_once()
    call_kwargs = mock_agent.fix.call_args
    assert call_kwargs.kwargs.get("create_mr") is False


# ---------------------------------------------------------------------------
# Fix mode — multiple jobs with same error (dedup)
# ---------------------------------------------------------------------------


async def test_fix_mode_multiple_jobs_same_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error dedup groups jobs with same error signature correctly."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    # Same error log for both jobs (will be deduplicated)
    same_log = "FAILED tests/test_foo.py::test_bar - AssertionError: assert 1 == 2\n1 failed\n"
    failed_jobs = [
        {"id": "201", "name": "test-py38", "status": "failed"},
        {"id": "202", "name": "test-py39", "status": "failed"},
    ]
    mock_adapter = _make_mock_adapter(list_failed_jobs=failed_jobs, fetch_job_logs=same_log)
    mock_result = _make_fix_result(status="fixed")

    mock_agent = AsyncMock()
    mock_agent.fix = AsyncMock(return_value=mock_result)

    with (
        patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter),
        patch("runners.ci_runner.StitchAgent", return_value=mock_agent),
    ):
        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    # Should only fix once (deduplication)
    assert mock_agent.fix.call_count == 1


# ---------------------------------------------------------------------------
# Verify mode — CI passed
# ---------------------------------------------------------------------------


async def test_verify_mode_ci_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """On stitch/fix-* branch with CI passed, creates MR."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "500")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import\n\nStitch-Target: main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    mock_adapter = _make_mock_adapter(
        list_failed_jobs=[],
        create_merge_request_url="https://gitlab.example.com/p/mr/55",
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_adapter.create_merge_request.assert_called_once()
    call_kwargs = mock_adapter.create_merge_request.call_args
    assert call_kwargs.kwargs["fix_branch"] == "stitch/fix-100"
    assert call_kwargs.kwargs["request"].branch == "main"


# ---------------------------------------------------------------------------
# Verify mode — no target branch
# ---------------------------------------------------------------------------


async def test_verify_mode_no_target_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing Stitch-Target trailer in commit message causes error."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "500")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    mock_adapter = _make_mock_adapter(
        get_latest_commit_message="fix(lint): remove unused import"
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 1
    mock_adapter.create_merge_request.assert_not_called()


# ---------------------------------------------------------------------------
# Retry mode — retry succeeds
# ---------------------------------------------------------------------------


async def test_retry_mode_retry_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed on stitch/fix-*, retry_fix returns fixed status."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "501")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import\n\nStitch-Target: main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    failed_jobs = [{"id": "300", "name": "check", "status": "failed"}]
    mock_adapter = _make_mock_adapter(
        list_failed_jobs=failed_jobs,
        count_branch_commits=1,  # 1 attempt so far, retry allowed
    )

    retry_result = FixResult(
        status="fixed",
        error_type=ErrorType.SIMPLE_TYPE,
        confidence=0.88,
        reason="Retry fixed the type error",
        fix_branch="stitch/fix-100",
    )

    mock_agent = AsyncMock()
    mock_agent.retry_fix = AsyncMock(return_value=retry_result)

    with (
        patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter),
        patch("runners.ci_runner.StitchAgent", return_value=mock_agent),
    ):
        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_agent.retry_fix.assert_called_once()


# ---------------------------------------------------------------------------
# Retry mode — exhausted
# ---------------------------------------------------------------------------


async def test_retry_mode_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Max attempts reached, escalates (exits with code 1)."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "501")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import\n\nStitch-Target: main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    failed_jobs = [{"id": "300", "name": "check", "status": "failed"}]
    mock_adapter = _make_mock_adapter(
        list_failed_jobs=failed_jobs,
        count_branch_commits=3,  # 3 commits == max_attempts (default 3)
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 1
    mock_adapter.create_merge_request.assert_not_called()


# ---------------------------------------------------------------------------
# JSON output format
# ---------------------------------------------------------------------------


async def test_json_output_fix_mode_no_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON output format includes status field on no failures."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    mock_adapter = _make_mock_adapter(list_failed_jobs=[])

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        exit_code = await run_ci(output_format="json", platform_override="gitlab")

    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "no_failures"
    assert "jobs" in data


async def test_json_output_fix_applied(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON output includes job results with usage stats when fix applied."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    failed_jobs = [{"id": "200", "name": "lint", "status": "failed"}]
    mock_adapter = _make_mock_adapter(list_failed_jobs=failed_jobs)
    mock_result = _make_fix_result(status="fixed", fix_branch="stitch/fix-100")

    mock_agent = AsyncMock()
    mock_agent.fix = AsyncMock(return_value=mock_result)

    with (
        patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter),
        patch("runners.ci_runner.StitchAgent", return_value=mock_agent),
    ):
        exit_code = await run_ci(output_format="json", platform_override="gitlab")

    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "complete"
    assert len(data["jobs"]) == 1
    job_result = data["jobs"][0]
    assert job_result["status"] == "fixed"
    assert job_result["fix_branch"] == "stitch/fix-100"
    assert "usage" in job_result


async def test_json_output_verify_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON output for verify mode includes mr_url and fix_branch."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "500")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): clean up\n\nStitch-Target: main")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_OPENROUTER_API_KEY", "fake-key")

    mock_adapter = _make_mock_adapter(
        list_failed_jobs=[],
        create_merge_request_url="https://gitlab.example.com/p/mr/77",
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        exit_code = await run_ci(output_format="json", platform_override="gitlab")

    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "verified"
    assert data["fix_branch"] == "stitch/fix-100"
    assert data["target_branch"] == "main"
    assert data["mr_url"] == "https://gitlab.example.com/p/mr/77"
