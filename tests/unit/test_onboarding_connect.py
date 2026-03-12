from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from stitch_agent.onboarding.connect import run_connect
from stitch_agent.settings import StitchSettings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_connect_prompts_when_required_inputs_missing(tmp_path: Path) -> None:
    settings = StitchSettings(github_token="", webhook_secret="")

    report = await run_connect(
        platform="github",
        repo_root=tmp_path,
        project_id=None,
        webhook_url=None,
        settings=settings,
    )

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 2
    assert report.ok is False
    assert checks["credentials.github_token"].status == "fail"
    assert checks["credentials.webhook_secret"].status == "fail"
    assert checks["repo.project_id"].status == "fail"
    assert checks["input.webhook_url"].status == "fail"
    assert len(report.prompts) >= 3


@pytest.mark.asyncio
@respx.mock
async def test_connect_creates_github_webhook(tmp_path: Path) -> None:
    settings = StitchSettings(github_token="ghp-test", webhook_secret="secret-value")
    webhook_url = "https://stitch.example.com/webhook/github"

    respx.get("https://api.github.com/repos/acme/repo").mock(return_value=httpx.Response(200))
    respx.get("https://api.github.com/repos/acme/repo/hooks").mock(
        return_value=httpx.Response(200, json=[])
    )
    post_route = respx.post("https://api.github.com/repos/acme/repo/hooks").mock(
        return_value=httpx.Response(201, json={"id": 10})
    )

    report = await run_connect(
        platform="github",
        repo_root=tmp_path,
        project_id="acme/repo",
        webhook_url=webhook_url,
        settings=settings,
    )

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 0
    assert report.ok is True
    assert checks["connect.webhook"].status == "pass"
    assert any(
        "Provisioned GitHub workflow_run webhook" in action for action in report.actions_taken
    )
    assert post_route.called


@pytest.mark.asyncio
@respx.mock
async def test_connect_skips_when_webhook_already_exists(tmp_path: Path) -> None:
    settings = StitchSettings(github_token="ghp-test", webhook_secret="secret-value")
    webhook_url = "https://stitch.example.com/webhook/github"

    respx.get("https://api.github.com/repos/acme/repo").mock(return_value=httpx.Response(200))
    respx.get("https://api.github.com/repos/acme/repo/hooks").mock(
        return_value=httpx.Response(200, json=[{"id": 12, "config": {"url": webhook_url}}])
    )
    post_route = respx.post("https://api.github.com/repos/acme/repo/hooks").mock(
        return_value=httpx.Response(201, json={"id": 15})
    )

    report = await run_connect(
        platform="github",
        repo_root=tmp_path,
        project_id="acme/repo",
        webhook_url=webhook_url,
        settings=settings,
    )

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 0
    assert report.ok is True
    assert checks["connect.webhook"].status == "pass"
    assert any("already exists" in action for action in report.actions_skipped)
    assert not post_route.called


@pytest.mark.asyncio
@respx.mock
async def test_connect_creates_gitlab_webhook(tmp_path: Path) -> None:
    settings = StitchSettings(gitlab_token="glpat-test", webhook_secret="secret-value")
    webhook_url = "https://stitch.example.com/webhook/gitlab"

    respx.get("https://gitlab.com/api/v4/projects/acme%2Frepo").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    respx.get("https://gitlab.com/api/v4/projects/acme%2Frepo/hooks").mock(
        return_value=httpx.Response(200, json=[])
    )
    post_route = respx.post("https://gitlab.com/api/v4/projects/acme%2Frepo/hooks").mock(
        return_value=httpx.Response(201, json={"id": 22})
    )

    report = await run_connect(
        platform="gitlab",
        repo_root=tmp_path,
        project_id="acme/repo",
        webhook_url=webhook_url,
        settings=settings,
    )

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 0
    assert report.ok is True
    assert checks["connect.webhook"].status == "pass"
    assert any("Provisioned GitLab pipeline webhook" in action for action in report.actions_taken)
    assert post_route.called


@pytest.mark.asyncio
@respx.mock
async def test_connect_skips_when_gitlab_webhook_already_exists(tmp_path: Path) -> None:
    settings = StitchSettings(gitlab_token="glpat-test", webhook_secret="secret-value")
    webhook_url = "https://stitch.example.com/webhook/gitlab"

    respx.get("https://gitlab.com/api/v4/projects/acme%2Frepo").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    respx.get("https://gitlab.com/api/v4/projects/acme%2Frepo/hooks").mock(
        return_value=httpx.Response(200, json=[{"id": 40, "url": webhook_url}])
    )
    post_route = respx.post("https://gitlab.com/api/v4/projects/acme%2Frepo/hooks").mock(
        return_value=httpx.Response(201, json={"id": 41})
    )

    report = await run_connect(
        platform="gitlab",
        repo_root=tmp_path,
        project_id="acme/repo",
        webhook_url=webhook_url,
        settings=settings,
    )

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 0
    assert report.ok is True
    assert checks["connect.webhook"].status == "pass"
    assert any("already exists" in action for action in report.actions_skipped)
    assert not post_route.called
