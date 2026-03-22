from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from runners.ci_runner import CIContext, build_context, detect_platform

if TYPE_CHECKING:
    pass

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
    import tempfile

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

    from stitch_agent.models import FixResult

    mock_result = FixResult(
        status="fixed",
        error_type="lint",
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
