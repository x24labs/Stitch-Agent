from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from runners.ci_runner import _is_stitch_branch, build_context, detect_platform

pytestmark = pytest.mark.asyncio


# --- detect_platform ---


def test_detect_platform_gitlab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    assert detect_platform() == "gitlab"


def test_detect_platform_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI_PROJECT_ID", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    assert detect_platform() == "github"


def test_detect_platform_override() -> None:
    assert detect_platform("gitlab") == "gitlab"
    assert detect_platform("github") == "github"


def test_detect_platform_unknown_override() -> None:
    with pytest.raises(SystemExit, match="Unknown platform"):
        detect_platform("bitbucket")


def test_detect_platform_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI_PROJECT_ID", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(SystemExit, match="Cannot detect CI platform"):
        detect_platform()


# --- build_context: GitLab after_script ---


def test_build_context_gitlab_after_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.setenv("CI_SERVER_URL", "https://gitlab.example.com")
    monkeypatch.setenv("CI_JOB_STATUS", "failed")
    monkeypatch.setenv("CI_JOB_ID", "200")
    monkeypatch.setenv("CI_JOB_NAME", "lint")

    ctx = build_context("gitlab")
    assert ctx.platform == "gitlab"
    assert ctx.project_id == "42"
    assert ctx.pipeline_id == "100"
    assert ctx.branch == "main"
    assert ctx.job_id == "200"
    assert ctx.job_name == "lint"
    assert ctx.base_url == "https://gitlab.example.com"


# --- build_context: GitLab .post stage ---


def test_build_context_gitlab_post_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "feature/x")
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)

    ctx = build_context("gitlab")
    assert ctx.platform == "gitlab"
    assert ctx.job_id is None  # needs discovery
    assert ctx.branch == "feature/x"


# --- build_context: GitHub ---


def test_build_context_github(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    event = {
        "workflow_run": {
            "id": 55555,
            "head_branch": "feat/stuff",
        }
    }
    event_file = tmp_path / "event.json"  # type: ignore[operator]
    event_file.write_text(json.dumps(event))

    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    monkeypatch.delenv("GITHUB_API_URL", raising=False)

    ctx = build_context("github")
    assert ctx.platform == "github"
    assert ctx.project_id == "acme/app"
    assert ctx.pipeline_id == "55555"
    assert ctx.branch == "feat/stuff"
    assert ctx.job_id is None


def test_build_context_github_no_event_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    with pytest.raises(SystemExit, match="GITHUB_EVENT_PATH not set"):
        build_context("github")


def test_build_context_github_no_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    event_file = tmp_path / "event.json"  # type: ignore[operator]
    event_file.write_text(json.dumps({"action": "completed"}))

    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/app")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))

    with pytest.raises(SystemExit, match="No workflow_run.id"):
        build_context("github")


# --- run_ci integration ---


async def test_run_ci_no_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    mock_adapter = AsyncMock()
    mock_adapter.list_failed_jobs = AsyncMock(return_value=[])
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0


async def test_run_ci_after_script_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.setenv("CI_JOB_STATUS", "failed")
    monkeypatch.setenv("CI_JOB_ID", "200")
    monkeypatch.setenv("CI_JOB_NAME", "lint")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    from stitch_agent.models import ErrorType, FixResult

    mock_result = FixResult(
        status="fixed",
        error_type=ErrorType.LINT,
        confidence=0.95,
        reason="Fixed lint error",
        mr_url="https://gitlab.com/mr/1",
        fix_branch="stitch/fix-abc",
    )

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)

    mock_agent = AsyncMock()
    mock_agent.fix = AsyncMock(return_value=mock_result)

    with (
        patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter),
        patch("runners.ci_runner.StitchAgent", return_value=mock_agent),
    ):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_agent.fix.assert_called_once()
    call_request = mock_agent.fix.call_args[0][0]
    assert call_request.job_id == "200"
    assert call_request.job_name == "lint"


# --- _is_stitch_branch ---


def test_is_stitch_branch_positive() -> None:
    assert _is_stitch_branch("stitch/fix-100") is True
    assert _is_stitch_branch("stitch/fix-abc") is True


def test_is_stitch_branch_negative() -> None:
    assert _is_stitch_branch("main") is False
    assert _is_stitch_branch("feature/stitch/fix-100") is False
    assert _is_stitch_branch("stitch-fix-100") is False


# --- verify mode ---


async def test_run_ci_verify_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "500")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import\n\nStitch-Target: main")
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)
    mock_adapter.list_failed_jobs = AsyncMock(return_value=[])
    mock_adapter.create_merge_request = AsyncMock(
        return_value="https://gitlab.com/mr/99"
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_adapter.create_merge_request.assert_called_once()
    call_kwargs = mock_adapter.create_merge_request.call_args
    assert call_kwargs.kwargs["fix_branch"] == "stitch/fix-100"
    assert call_kwargs.kwargs["request"].branch == "main"
    # Should NOT call API since CI_COMMIT_MESSAGE has the target
    mock_adapter.get_latest_commit_message.assert_not_called()


async def test_run_ci_verify_uses_api_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When CI_COMMIT_MESSAGE is absent, falls back to API call."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "500")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.delenv("CI_COMMIT_MESSAGE", raising=False)
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)
    mock_adapter.get_latest_commit_message = AsyncMock(
        return_value="fix(lint): remove unused import\n\nStitch-Target: develop"
    )
    mock_adapter.list_failed_jobs = AsyncMock(return_value=[])
    mock_adapter.create_merge_request = AsyncMock(
        return_value="https://gitlab.com/mr/101"
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_adapter.get_latest_commit_message.assert_called_once()
    call_kwargs = mock_adapter.create_merge_request.call_args
    assert call_kwargs.kwargs["request"].branch == "develop"


async def test_run_ci_verify_api_fallback_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both CI_COMMIT_MESSAGE and API fail, return error gracefully."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "500")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.delenv("CI_COMMIT_MESSAGE", raising=False)
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)
    mock_adapter.get_latest_commit_message = AsyncMock(
        side_effect=Exception("404 Not Found")
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 1  # error: no target branch found
    mock_adapter.create_merge_request.assert_not_called()


async def test_run_ci_verify_mode_no_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "500")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import")
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)
    mock_adapter.get_latest_commit_message = AsyncMock(
        return_value="fix(lint): remove unused import"
    )

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 1


async def test_run_ci_escalate_when_fix_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """When max retry attempts exhausted on stitch/fix-* branch, escalate to human."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "501")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import\n\nStitch-Target: main")
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)
    mock_adapter.list_failed_jobs = AsyncMock(
        return_value=[{"id": "300", "name": "check", "status": "failed"}]
    )
    # Simulate max attempts reached (3 commits = 3 attempts)
    mock_adapter.count_branch_commits = AsyncMock(return_value=3)

    with patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 1
    mock_adapter.create_merge_request.assert_not_called()


async def test_run_ci_retry_when_fix_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """When fix fails CI with attempts remaining, retry on the same branch."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "501")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "stitch/fix-100")
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "fix(lint): remove unused import\n\nStitch-Target: main")
    monkeypatch.delenv("CI_JOB_STATUS", raising=False)
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)
    mock_adapter.list_failed_jobs = AsyncMock(
        return_value=[{"id": "300", "name": "check", "status": "failed"}]
    )
    # Only 1 attempt so far — should retry
    mock_adapter.count_branch_commits = AsyncMock(return_value=1)

    from stitch_agent.models import ErrorType, FixResult

    mock_fix_result = FixResult(
        status="fixed",
        error_type=ErrorType.SIMPLE_TYPE,
        confidence=0.95,
        reason="Fixed syntax error",
        fix_branch="stitch/fix-100",
    )

    with (
        patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter),
        patch("runners.ci_runner.StitchAgent") as MockAgent,
    ):
        mock_agent_instance = AsyncMock()
        mock_agent_instance.retry_fix = AsyncMock(return_value=mock_fix_result)
        MockAgent.return_value = mock_agent_instance

        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_agent_instance.retry_fix.assert_called_once()


async def test_run_ci_fix_mode_no_mr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix mode should call agent.fix with create_mr=False."""
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("CI_PIPELINE_ID", "100")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", "main")
    monkeypatch.setenv("CI_JOB_STATUS", "failed")
    monkeypatch.setenv("CI_JOB_ID", "200")
    monkeypatch.setenv("CI_JOB_NAME", "lint")
    monkeypatch.delenv("CI_SERVER_URL", raising=False)
    monkeypatch.setenv("STITCH_GITLAB_TOKEN", "fake-token")
    monkeypatch.setenv("STITCH_ANTHROPIC_API_KEY", "fake-key")

    from stitch_agent.models import ErrorType, FixResult

    mock_result = FixResult(
        status="fixed",
        error_type=ErrorType.LINT,
        confidence=0.95,
        reason="Fixed lint error",
        fix_branch="stitch/fix-100",
    )

    mock_adapter = AsyncMock()
    mock_adapter.__aenter__ = AsyncMock(return_value=mock_adapter)
    mock_adapter.__aexit__ = AsyncMock(return_value=False)

    mock_agent = AsyncMock()
    mock_agent.fix = AsyncMock(return_value=mock_result)

    with (
        patch("stitch_agent.adapters.gitlab.GitLabAdapter", return_value=mock_adapter),
        patch("runners.ci_runner.StitchAgent", return_value=mock_agent),
    ):
        from runners.ci_runner import run_ci

        exit_code = await run_ci(output_format="text", platform_override="gitlab")

    assert exit_code == 0
    mock_agent.fix.assert_called_once()
    # Verify create_mr=False is passed
    call_kwargs = mock_agent.fix.call_args
    assert call_kwargs.kwargs.get("create_mr") is False
