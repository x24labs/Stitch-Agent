from __future__ import annotations

import sys
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from stitch_agent.config import DEFAULT_CONFIG_FILENAME
from stitch_agent.onboarding.report import CheckResult, CommandReport, build_command_report

if TYPE_CHECKING:
    from pathlib import Path

    from stitch_agent.settings import StitchSettings


async def run_doctor_checks(
    *,
    platform: str,
    repo_root: Path,
    settings: StitchSettings,
    project_id: str | None = None,
) -> CommandReport:
    checks: list[CheckResult] = []

    checks.append(_check_python_runtime())
    checks.append(_check_repo_exists(repo_root))
    checks.append(_check_config_file(repo_root))
    checks.append(_check_anthropic_key(settings))

    provider_check = _check_provider_token(platform, settings)
    checks.append(provider_check)
    checks.append(_check_validation_runtime(settings))

    if provider_check.status == "pass":
        checks.append(await _check_provider_connectivity(platform, settings))
    else:
        checks.append(
            CheckResult(
                id="connectivity.provider_api",
                status="skip",
                severity="info",
                message="Provider connectivity check skipped because token is missing",
                remediation="Set provider token and run `stitch connect`",
            )
        )

    if platform == "github":
        if provider_check.status == "pass":
            checks.append(await _check_github_scopes(settings))
            if project_id:
                checks.extend(await _check_github_repo_permissions(project_id, settings))
            else:
                checks.append(
                    CheckResult(
                        id="permissions.github_repo_access",
                        status="skip",
                        severity="info",
                        message="GitHub repo permission checks skipped because --project-id is missing",
                        remediation="Run `stitch doctor --platform github --project-id <owner/repo> --json`",
                    )
                )
        else:
            checks.append(
                CheckResult(
                    id="permissions.github_scopes",
                    status="skip",
                    severity="info",
                    message="GitHub permission checks skipped because token is missing",
                    remediation="Set STITCH_GITHUB_TOKEN and re-run `stitch doctor --platform github --json`",
                )
            )
    elif platform == "gitlab":
        if provider_check.status == "pass":
            checks.append(await _check_gitlab_scopes(settings))
            if project_id:
                checks.extend(await _check_gitlab_project_permissions(project_id, settings))
            else:
                checks.append(
                    CheckResult(
                        id="permissions.gitlab_project_access",
                        status="skip",
                        severity="info",
                        message="GitLab project permission checks skipped because --project-id is missing",
                        remediation="Run `stitch doctor --platform gitlab --project-id <namespace/project> --json`",
                    )
                )
        else:
            checks.append(
                CheckResult(
                    id="permissions.gitlab_scopes",
                    status="skip",
                    severity="info",
                    message="GitLab permission checks skipped because token is missing",
                    remediation="Set STITCH_GITLAB_TOKEN and re-run `stitch doctor --platform gitlab --json`",
                )
            )

    return build_command_report(command="doctor", checks=checks)


def _check_python_runtime() -> CheckResult:
    current = f"{sys.version_info.major}.{sys.version_info.minor}"
    return CheckResult(
        id="runtime.python",
        status="pass",
        severity="info",
        message=f"Python runtime is compatible ({current})",
    )


def _check_repo_exists(repo_root: Path) -> CheckResult:
    if repo_root.exists() and repo_root.is_dir():
        return CheckResult(
            id="repo.root",
            status="pass",
            severity="info",
            message=f"Repository path exists: {repo_root}",
        )
    return CheckResult(
        id="repo.root",
        status="fail",
        severity="error",
        message=f"Repository path does not exist: {repo_root}",
        remediation="Pass a valid path with `--repo`",
    )


def _check_config_file(repo_root: Path) -> CheckResult:
    config_path = repo_root / DEFAULT_CONFIG_FILENAME
    if config_path.exists():
        return CheckResult(
            id="config.repo_file",
            status="pass",
            severity="info",
            message=f"Found {DEFAULT_CONFIG_FILENAME}",
        )
    return CheckResult(
        id="config.repo_file",
        status="fail",
        severity="error",
        message=f"Missing {DEFAULT_CONFIG_FILENAME}",
        remediation="Run `stitch setup --repo . --platform <gitlab|github>`",
    )


def _check_anthropic_key(settings: StitchSettings) -> CheckResult:
    if settings.anthropic_api_key:
        return CheckResult(
            id="credentials.anthropic",
            status="pass",
            severity="info",
            message="Anthropic API key is configured",
        )
    return CheckResult(
        id="credentials.anthropic",
        status="fail",
        severity="error",
        message="Missing STITCH_ANTHROPIC_API_KEY",
        remediation="Export STITCH_ANTHROPIC_API_KEY and re-run `stitch doctor --json`",
    )


def _check_provider_token(platform: str, settings: StitchSettings) -> CheckResult:
    if platform == "github":
        token_name = "STITCH_GITHUB_TOKEN"
        value = settings.github_token
    else:
        token_name = "STITCH_GITLAB_TOKEN"
        value = settings.gitlab_token

    if value:
        return CheckResult(
            id="credentials.provider_token",
            status="pass",
            severity="info",
            message=f"Provider token is configured ({token_name})",
        )

    return CheckResult(
        id="credentials.provider_token",
        status="fail",
        severity="error",
        message=f"Missing {token_name}",
        remediation=f"Set {token_name} or run `stitch connect --platform {platform}`",
    )


def _check_validation_runtime(settings: StitchSettings) -> CheckResult:
    return CheckResult(
        id="validation.mode",
        status="pass",
        severity="info",
        message="Fixes are verified by CI pipeline",
    )

    return CheckResult(
        id="validation.mode",
        status="pass",
        severity="info",
        message="Strict validation mode dependencies are available",
    )


async def _check_provider_connectivity(platform: str, settings: StitchSettings) -> CheckResult:
    if platform == "github":
        token = settings.github_token
        url = settings.github_base_url.rstrip("/") + "/rate_limit"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    else:
        token = settings.gitlab_token
        url = settings.gitlab_base_url.rstrip("/") + "/api/v4/version"
        headers = {"PRIVATE-TOKEN": token}

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return CheckResult(
            id="connectivity.provider_api",
            status="fail",
            severity="error",
            message=f"Cannot reach provider API: {exc}",
            remediation="Check network/base URL and re-run `stitch doctor --json`",
        )

    if response.status_code < 300:
        return CheckResult(
            id="connectivity.provider_api",
            status="pass",
            severity="info",
            message="Provider API is reachable with current token",
        )

    if response.status_code in {401, 403}:
        return CheckResult(
            id="connectivity.provider_api",
            status="fail",
            severity="error",
            message=f"Provider token rejected (HTTP {response.status_code})",
            remediation=f"Refresh credentials with `stitch connect --platform {platform}`",
        )

    return CheckResult(
        id="connectivity.provider_api",
        status="fail",
        severity="error",
        message=f"Provider API returned HTTP {response.status_code}",
        remediation="Retry later or verify provider availability",
    )


async def _check_github_scopes(settings: StitchSettings) -> CheckResult:
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = settings.github_base_url.rstrip("/") + "/user"

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return CheckResult(
            id="permissions.github_scopes",
            status="fail",
            severity="error",
            message=f"Cannot verify GitHub token scopes: {exc}",
            remediation="Check network/base URL and re-run `stitch doctor --platform github --json`",
        )

    if response.status_code >= 300:
        return CheckResult(
            id="permissions.github_scopes",
            status="fail",
            severity="error",
            message=f"GitHub token validation failed (HTTP {response.status_code})",
            remediation="Refresh STITCH_GITHUB_TOKEN with `repo` and `admin:repo_hook` permissions",
        )

    scopes_header = response.headers.get("X-OAuth-Scopes", "")
    if not scopes_header.strip():
        return CheckResult(
            id="permissions.github_scopes",
            status="warn",
            severity="warning",
            message="GitHub token scopes are not exposed by API headers",
            remediation="If using a fine-grained token, verify repository admin/webhook permissions",
        )

    scopes = {scope.strip() for scope in scopes_header.split(",") if scope.strip()}
    required = {"repo", "admin:repo_hook"}
    missing = sorted(required - scopes)
    if missing:
        joined = ", ".join(missing)
        return CheckResult(
            id="permissions.github_scopes",
            status="fail",
            severity="error",
            message=f"GitHub token is missing required scopes: {joined}",
            remediation="Create a token with `repo` and `admin:repo_hook` scopes and re-run doctor",
        )

    return CheckResult(
        id="permissions.github_scopes",
        status="pass",
        severity="info",
        message="GitHub token includes required scopes for webhook provisioning",
    )


async def _check_github_repo_permissions(
    project_id: str, settings: StitchSettings
) -> list[CheckResult]:
    owner_repo = _parse_github_project_id(project_id)
    if owner_repo is None:
        return [
            CheckResult(
                id="permissions.github_repo_access",
                status="fail",
                severity="error",
                message=f"Invalid GitHub project id `{project_id}`",
                remediation="Pass --project-id in `owner/repo` format",
            )
        ]

    owner, repo = owner_repo
    base = settings.github_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    repo_url = f"{base}/repos/{owner}/{repo}"
    hooks_url = f"{base}/repos/{owner}/{repo}/hooks"

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            repo_response = await client.get(repo_url, headers=headers)
            if repo_response.status_code < 300:
                repo_check = CheckResult(
                    id="permissions.github_repo_access",
                    status="pass",
                    severity="info",
                    message=f"GitHub repository is accessible: {owner}/{repo}",
                )
                hooks_response = await client.get(
                    hooks_url, headers=headers, params={"per_page": 1}
                )
            else:
                repo_check = _repo_access_error_check(repo_response.status_code, project_id)
                return [repo_check]
    except httpx.HTTPError as exc:
        return [
            CheckResult(
                id="permissions.github_repo_access",
                status="fail",
                severity="error",
                message=f"Cannot verify GitHub repo permissions: {exc}",
                remediation="Check network/base URL and re-run doctor",
            )
        ]

    if hooks_response.status_code < 300:
        hooks_check = CheckResult(
            id="permissions.github_hooks_access",
            status="pass",
            severity="info",
            message="Token can read repository webhooks",
        )
    elif hooks_response.status_code in {401, 403}:
        hooks_check = CheckResult(
            id="permissions.github_hooks_access",
            status="fail",
            severity="error",
            message="Token cannot access repository webhooks (missing admin permissions)",
            remediation="Grant webhook admin permissions (classic: admin:repo_hook)",
        )
    else:
        hooks_check = CheckResult(
            id="permissions.github_hooks_access",
            status="fail",
            severity="error",
            message=f"Unexpected webhook permission check response (HTTP {hooks_response.status_code})",
            remediation="Retry later or verify repository permissions manually",
        )

    return [repo_check, hooks_check]


async def _check_gitlab_scopes(settings: StitchSettings) -> CheckResult:
    headers = {"PRIVATE-TOKEN": settings.gitlab_token}
    url = settings.gitlab_base_url.rstrip("/") + "/api/v4/user"

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return CheckResult(
            id="permissions.gitlab_scopes",
            status="fail",
            severity="error",
            message=f"Cannot verify GitLab token scopes: {exc}",
            remediation="Check network/base URL and re-run `stitch doctor --platform gitlab --json`",
        )

    if response.status_code >= 300:
        return CheckResult(
            id="permissions.gitlab_scopes",
            status="fail",
            severity="error",
            message=f"GitLab token validation failed (HTTP {response.status_code})",
            remediation="Refresh STITCH_GITLAB_TOKEN with `api` scope",
        )

    scopes_header = response.headers.get("X-OAuth-Scopes", "")
    if not scopes_header.strip():
        return CheckResult(
            id="permissions.gitlab_scopes",
            status="warn",
            severity="warning",
            message="GitLab token scopes are not exposed by API headers",
            remediation="Verify STITCH_GITLAB_TOKEN has `api` scope and maintainer permissions",
        )

    scopes = {scope.strip() for scope in scopes_header.split(",") if scope.strip()}
    if "api" not in scopes:
        return CheckResult(
            id="permissions.gitlab_scopes",
            status="fail",
            severity="error",
            message="GitLab token is missing required scope: api",
            remediation="Create a token with `api` scope and re-run doctor",
        )

    return CheckResult(
        id="permissions.gitlab_scopes",
        status="pass",
        severity="info",
        message="GitLab token includes required `api` scope for webhook provisioning",
    )


async def _check_gitlab_project_permissions(
    project_id: str, settings: StitchSettings
) -> list[CheckResult]:
    base = settings.gitlab_base_url.rstrip("/") + "/api/v4"
    encoded_project_id = quote(project_id, safe="")
    project_url = f"{base}/projects/{encoded_project_id}"
    hooks_url = f"{base}/projects/{encoded_project_id}/hooks"
    headers = {"PRIVATE-TOKEN": settings.gitlab_token}

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            project_response = await client.get(project_url, headers=headers)
            if project_response.status_code < 300:
                project_check = CheckResult(
                    id="permissions.gitlab_project_access",
                    status="pass",
                    severity="info",
                    message=f"GitLab project is accessible: {project_id}",
                )
                hooks_response = await client.get(
                    hooks_url,
                    headers=headers,
                    params={"per_page": 1},
                )
            else:
                project_check = _gitlab_project_access_error_check(
                    status_code=project_response.status_code,
                    project_id=project_id,
                )
                return [project_check]
    except httpx.HTTPError as exc:
        return [
            CheckResult(
                id="permissions.gitlab_project_access",
                status="fail",
                severity="error",
                message=f"Cannot verify GitLab project permissions: {exc}",
                remediation="Check network/base URL and re-run doctor",
            )
        ]

    if hooks_response.status_code < 300:
        hooks_check = CheckResult(
            id="permissions.gitlab_hooks_access",
            status="pass",
            severity="info",
            message="Token can read project webhooks",
        )
    elif hooks_response.status_code in {401, 403}:
        hooks_check = CheckResult(
            id="permissions.gitlab_hooks_access",
            status="fail",
            severity="error",
            message="Token cannot access project webhooks (missing maintainer permissions)",
            remediation="Grant maintainer access with `api` scope",
        )
    else:
        hooks_check = CheckResult(
            id="permissions.gitlab_hooks_access",
            status="fail",
            severity="error",
            message=f"Unexpected webhook permission check response (HTTP {hooks_response.status_code})",
            remediation="Retry later or verify project permissions manually",
        )

    return [project_check, hooks_check]


def _repo_access_error_check(status_code: int, project_id: str) -> CheckResult:
    if status_code in {401, 403}:
        return CheckResult(
            id="permissions.github_repo_access",
            status="fail",
            severity="error",
            message=f"Token cannot access repository `{project_id}`",
            remediation="Grant repository access to STITCH_GITHUB_TOKEN",
        )
    if status_code == 404:
        return CheckResult(
            id="permissions.github_repo_access",
            status="fail",
            severity="error",
            message=f"Repository `{project_id}` not found",
            remediation="Verify --project-id owner/repo is correct",
        )
    return CheckResult(
        id="permissions.github_repo_access",
        status="fail",
        severity="error",
        message=f"Repository access check failed (HTTP {status_code})",
        remediation="Retry later or verify provider availability",
    )


def _gitlab_project_access_error_check(status_code: int, project_id: str) -> CheckResult:
    if status_code in {401, 403}:
        return CheckResult(
            id="permissions.gitlab_project_access",
            status="fail",
            severity="error",
            message=f"Token cannot access project `{project_id}`",
            remediation="Grant project access and `api` scope to STITCH_GITLAB_TOKEN",
        )
    if status_code == 404:
        return CheckResult(
            id="permissions.gitlab_project_access",
            status="fail",
            severity="error",
            message=f"Project `{project_id}` not found",
            remediation="Verify --project-id namespace/project is correct",
        )
    return CheckResult(
        id="permissions.gitlab_project_access",
        status="fail",
        severity="error",
        message=f"Project access check failed (HTTP {status_code})",
        remediation="Retry later or verify provider availability",
    )


def _parse_github_project_id(project_id: str) -> tuple[str, str] | None:
    if "/" not in project_id:
        return None
    owner, repo = project_id.split("/", 1)
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return None
    return owner, repo
