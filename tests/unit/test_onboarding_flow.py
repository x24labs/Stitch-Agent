from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from stitch_agent.onboarding.connect import run_connect
from stitch_agent.onboarding.doctor import run_doctor_checks
from stitch_agent.onboarding.setup import run_setup
from stitch_agent.settings import StitchSettings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
@respx.mock
async def test_github_onboarding_flow_setup_connect_doctor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip()
        + "\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        """
[remote "origin"]
    url = git@github.com:acme/repo.git
""".strip()
        + "\n"
    )

    setup_report = run_setup(repo_root=tmp_path, platform="github")
    assert setup_report.exit_code() == 0

    respx.get("https://api.github.com/repos/acme/repo").mock(return_value=httpx.Response(200))
    respx.get("https://api.github.com/repos/acme/repo/hooks").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("https://api.github.com/repos/acme/repo/hooks").mock(
        return_value=httpx.Response(201, json={"id": 15})
    )
    respx.get("https://api.github.com/rate_limit").mock(return_value=httpx.Response(200))
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, headers={"X-OAuth-Scopes": "repo, admin:repo_hook"})
    )
    respx.get("https://api.github.com/repos/acme/repo/hooks", params={"per_page": "1"}).mock(
        return_value=httpx.Response(200, json=[])
    )

    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        github_token="ghp-test",
        webhook_secret="secret-value",

    )

    connect_report = await run_connect(
        platform="github",
        repo_root=tmp_path,
        project_id=None,
        webhook_url="https://stitch.example.com/webhook/github",
        settings=settings,
    )
    assert connect_report.exit_code() == 0

    doctor_report = await run_doctor_checks(
        platform="github",
        repo_root=tmp_path,
        settings=settings,
        project_id="acme/repo",
    )
    assert doctor_report.exit_code() == 0


@pytest.mark.asyncio
@respx.mock
async def test_gitlab_onboarding_flow_setup_connect_doctor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - test\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        """
[remote "origin"]
    url = git@gitlab.com:acme/repo.git
""".strip()
        + "\n"
    )

    setup_report = run_setup(repo_root=tmp_path, platform="gitlab")
    assert setup_report.exit_code() == 0

    respx.get("https://gitlab.com/api/v4/projects/acme%2Frepo").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )
    respx.get("https://gitlab.com/api/v4/projects/acme%2Frepo/hooks").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("https://gitlab.com/api/v4/projects/acme%2Frepo/hooks").mock(
        return_value=httpx.Response(201, json={"id": 31})
    )
    respx.get("https://gitlab.com/api/v4/version").mock(
        return_value=httpx.Response(200, json={"version": "17.0.0"})
    )
    respx.get("https://gitlab.com/api/v4/user").mock(
        return_value=httpx.Response(200, headers={"X-OAuth-Scopes": "api, read_api"})
    )
    respx.get(
        "https://gitlab.com/api/v4/projects/acme%2Frepo/hooks", params={"per_page": "1"}
    ).mock(return_value=httpx.Response(200, json=[]))

    settings = StitchSettings(
        anthropic_api_key="sk-ant-test",
        gitlab_token="glpat-test",
        webhook_secret="secret-value",

    )

    connect_report = await run_connect(
        platform="gitlab",
        repo_root=tmp_path,
        project_id=None,
        webhook_url="https://stitch.example.com/webhook/gitlab",
        settings=settings,
    )
    assert connect_report.exit_code() == 0

    doctor_report = await run_doctor_checks(
        platform="gitlab",
        repo_root=tmp_path,
        settings=settings,
        project_id="acme/repo",
    )
    assert doctor_report.exit_code() == 0
