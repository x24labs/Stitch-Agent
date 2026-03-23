from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from stitch_agent.onboarding.doctor import run_doctor_checks
from stitch_agent.settings import StitchSettings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
@respx.mock
async def test_doctor_returns_success_when_repo_ready(tmp_path: Path) -> None:
    (tmp_path / ".stitch.yml").write_text("languages: [python]\n")
    respx.get("https://gitlab.com/api/v4/version").mock(
        return_value=httpx.Response(200, json={"version": "17.0.0"})
    )
    respx.get("https://gitlab.com/api/v4/user").mock(
        return_value=httpx.Response(200, headers={"X-OAuth-Scopes": "api, read_api"})
    )

    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        gitlab_token="glpat-test",

    )

    report = await run_doctor_checks(platform="gitlab", repo_root=tmp_path, settings=settings)
    payload = report.to_dict()

    assert report.exit_code() == 0
    assert report.ok is True
    assert payload["command"] == "doctor"
    assert payload["schema_version"] == "1.0"
    assert payload["errors"] == []


@pytest.mark.asyncio
async def test_doctor_fails_when_config_file_missing(tmp_path: Path) -> None:
    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        gitlab_token="glpat-test",

    )

    report = await run_doctor_checks(platform="gitlab", repo_root=tmp_path, settings=settings)

    assert report.exit_code() == 1
    assert report.ok is False
    assert any(error.startswith("config.repo_file:") for error in report.errors)
    assert any("stitch setup" in step for step in report.next_steps)


@pytest.mark.asyncio
async def test_doctor_skips_connectivity_without_provider_token(tmp_path: Path) -> None:
    (tmp_path / ".stitch.yml").write_text("languages: [python]\n")
    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        gitlab_token="",

    )

    report = await run_doctor_checks(platform="gitlab", repo_root=tmp_path, settings=settings)

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 1
    assert checks["credentials.provider_token"].status == "fail"
    assert checks["connectivity.provider_api"].status == "skip"


@pytest.mark.asyncio
@respx.mock
async def test_doctor_reports_missing_required_github_scopes(tmp_path: Path) -> None:
    (tmp_path / ".stitch.yml").write_text("languages: [python]\n")
    respx.get("https://api.github.com/rate_limit").mock(return_value=httpx.Response(200))
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, headers={"X-OAuth-Scopes": "repo"})
    )

    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        github_token="ghp-test",

    )

    report = await run_doctor_checks(platform="github", repo_root=tmp_path, settings=settings)

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 1
    assert checks["permissions.github_scopes"].status == "fail"
    assert any("permissions.github_scopes" in error for error in report.errors)


@pytest.mark.asyncio
@respx.mock
async def test_doctor_checks_github_repo_and_hooks_permissions_when_project_id_set(
    tmp_path: Path,
) -> None:
    (tmp_path / ".stitch.yml").write_text("languages: [python]\n")
    respx.get("https://api.github.com/rate_limit").mock(return_value=httpx.Response(200))
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, headers={"X-OAuth-Scopes": "repo, admin:repo_hook"})
    )
    respx.get("https://api.github.com/repos/acme/repo").mock(return_value=httpx.Response(200))
    respx.get("https://api.github.com/repos/acme/repo/hooks", params={"per_page": "1"}).mock(
        return_value=httpx.Response(200, json=[])
    )

    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        github_token="ghp-test",

    )

    report = await run_doctor_checks(
        platform="github",
        repo_root=tmp_path,
        settings=settings,
        project_id="acme/repo",
    )

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 0
    assert checks["permissions.github_scopes"].status == "pass"
    assert checks["permissions.github_repo_access"].status == "pass"
    assert checks["permissions.github_hooks_access"].status == "pass"


@pytest.mark.asyncio
@respx.mock
async def test_doctor_reports_missing_required_gitlab_api_scope(tmp_path: Path) -> None:
    (tmp_path / ".stitch.yml").write_text("languages: [python]\n")
    respx.get("https://gitlab.com/api/v4/version").mock(
        return_value=httpx.Response(200, json={"version": "17.0.0"})
    )
    respx.get("https://gitlab.com/api/v4/user").mock(
        return_value=httpx.Response(200, headers={"X-OAuth-Scopes": "read_api"})
    )

    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        gitlab_token="glpat-test",

    )

    report = await run_doctor_checks(platform="gitlab", repo_root=tmp_path, settings=settings)

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 1
    assert checks["permissions.gitlab_scopes"].status == "fail"
    assert any("permissions.gitlab_scopes" in error for error in report.errors)


@pytest.mark.asyncio
@respx.mock
async def test_doctor_checks_gitlab_project_and_hooks_permissions_when_project_id_set(
    tmp_path: Path,
) -> None:
    (tmp_path / ".stitch.yml").write_text("languages: [python]\n")
    respx.get("https://gitlab.com/api/v4/version").mock(
        return_value=httpx.Response(200, json={"version": "17.0.0"})
    )
    respx.get("https://gitlab.com/api/v4/user").mock(
        return_value=httpx.Response(200, headers={"X-OAuth-Scopes": "api, read_api"})
    )
    respx.get("https://gitlab.com/api/v4/projects/acme%2Frepo").mock(
        return_value=httpx.Response(200, json={"id": 42})
    )
    respx.get(
        "https://gitlab.com/api/v4/projects/acme%2Frepo/hooks", params={"per_page": "1"}
    ).mock(return_value=httpx.Response(200, json=[]))

    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        gitlab_token="glpat-test",

    )

    report = await run_doctor_checks(
        platform="gitlab",
        repo_root=tmp_path,
        settings=settings,
        project_id="acme/repo",
    )

    checks = {check.id: check for check in report.checks}
    assert report.exit_code() == 0
    assert checks["permissions.gitlab_scopes"].status == "pass"
    assert checks["permissions.gitlab_project_access"].status == "pass"
    assert checks["permissions.gitlab_hooks_access"].status == "pass"
