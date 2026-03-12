from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

import httpx

from stitch_agent.onboarding.report import CheckResult, CommandReport, build_command_report

if TYPE_CHECKING:
    from pathlib import Path

    from stitch_agent.settings import StitchSettings


async def run_connect(
    *,
    platform: str,
    repo_root: Path,
    project_id: str | None,
    webhook_url: str | None,
    settings: StitchSettings,
) -> CommandReport:
    checks: list[CheckResult] = []
    prompts: list[str] = []
    actions_taken: list[str] = []
    actions_skipped: list[str] = []

    if not repo_root.exists() or not repo_root.is_dir():
        checks.append(
            CheckResult(
                id="repo.root",
                status="fail",
                severity="error",
                message=f"Repository path does not exist: {repo_root}",
                remediation="Pass a valid path with `--repo`",
            )
        )
        return build_command_report(command="connect", checks=checks)

    checks.append(
        CheckResult(
            id="repo.root",
            status="pass",
            severity="info",
            message=f"Repository path exists: {repo_root}",
        )
    )

    if platform == "gitlab":
        return await _run_gitlab_connect(
            checks=checks,
            prompts=prompts,
            actions_taken=actions_taken,
            actions_skipped=actions_skipped,
            repo_root=repo_root,
            project_id=project_id,
            webhook_url=webhook_url,
            settings=settings,
        )

    if platform != "github":
        checks.append(
            CheckResult(
                id="connect.platform",
                status="fail",
                severity="error",
                message=f"Unsupported platform `{platform}`",
                remediation="Use `stitch connect --platform <gitlab|github>`",
            )
        )
        return build_command_report(command="connect", checks=checks)

    github_token = getattr(settings, "github_token", "")
    webhook_secret = getattr(settings, "webhook_secret", "")
    github_base_url = getattr(settings, "github_base_url", "https://api.github.com")

    if github_token:
        checks.append(
            CheckResult(
                id="credentials.github_token",
                status="pass",
                severity="info",
                message="STITCH_GITHUB_TOKEN is configured",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="credentials.github_token",
                status="fail",
                severity="error",
                message="Missing STITCH_GITHUB_TOKEN",
                remediation="Export STITCH_GITHUB_TOKEN with repo and webhook admin permissions",
            )
        )
        prompts.append("Provide STITCH_GITHUB_TOKEN to continue automated connect")

    if webhook_secret:
        checks.append(
            CheckResult(
                id="credentials.webhook_secret",
                status="pass",
                severity="info",
                message="STITCH_WEBHOOK_SECRET is configured",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="credentials.webhook_secret",
                status="fail",
                severity="error",
                message="Missing STITCH_WEBHOOK_SECRET",
                remediation="Set STITCH_WEBHOOK_SECRET before provisioning provider webhooks",
            )
        )
        prompts.append("Provide STITCH_WEBHOOK_SECRET so signatures can be validated")

    resolved_project_id = project_id or _detect_github_project_id(repo_root)
    if resolved_project_id:
        checks.append(
            CheckResult(
                id="repo.project_id",
                status="pass",
                severity="info",
                message=f"Using GitHub project id: {resolved_project_id}",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="repo.project_id",
                status="fail",
                severity="error",
                message="Could not determine GitHub project id",
                remediation="Pass --project-id owner/repo or configure origin remote to GitHub",
            )
        )
        prompts.append("Provide --project-id in owner/repo format")

    if _is_valid_webhook_url(webhook_url):
        checks.append(
            CheckResult(
                id="input.webhook_url",
                status="pass",
                severity="info",
                message=f"Webhook target URL is valid: {webhook_url}",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="input.webhook_url",
                status="fail",
                severity="error",
                message="Missing or invalid webhook URL",
                remediation="Pass --webhook-url https://<public-host>/webhook/github",
            )
        )
        prompts.append("Provide --webhook-url to the publicly reachable stitch webhook endpoint")

    if prompts:
        return build_command_report(command="connect", checks=checks, prompts=prompts)

    assert resolved_project_id is not None
    assert webhook_url is not None

    owner, repo = _split_project_id(resolved_project_id)
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = github_base_url.rstrip("/")
    repo_url = f"{base}/repos/{owner}/{repo}"
    hooks_url = f"{base}/repos/{owner}/{repo}/hooks"

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            repo_response = await client.get(repo_url, headers=headers)
            if repo_response.status_code < 300:
                checks.append(
                    CheckResult(
                        id="connectivity.repo",
                        status="pass",
                        severity="info",
                        message=f"GitHub repository is reachable: {resolved_project_id}",
                    )
                )
            else:
                checks.append(
                    _repo_error_check(
                        status_code=repo_response.status_code,
                        project_id=resolved_project_id,
                    )
                )
                if repo_response.status_code in {401, 403, 404}:
                    prompts.append(
                        "Adjust token permissions or project id and retry `stitch connect`"
                    )
                return build_command_report(command="connect", checks=checks, prompts=prompts)

            hooks_response = await client.get(hooks_url, headers=headers)
            if hooks_response.status_code >= 300:
                checks.append(_hooks_error_check(hooks_response.status_code))
                if hooks_response.status_code in {401, 403}:
                    prompts.append("Grant webhook admin permissions to STITCH_GITHUB_TOKEN")
                return build_command_report(command="connect", checks=checks, prompts=prompts)

            hooks_payload = hooks_response.json() if hooks_response.content else []
            existing = _find_hook(hooks_payload, target_url=webhook_url)

            if existing:
                actions_skipped.append("GitHub webhook already exists for target URL")
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="pass",
                        severity="info",
                        message="Webhook already provisioned",
                    )
                )
                return build_command_report(
                    command="connect",
                    checks=checks,
                    actions_skipped=actions_skipped,
                    next_steps=[
                        f"Run `stitch doctor --platform github --project-id {resolved_project_id} --json`"
                    ],
                )

            create_response = await client.post(
                hooks_url,
                headers=headers,
                json={
                    "name": "web",
                    "active": True,
                    "events": ["workflow_run"],
                    "config": {
                        "url": webhook_url,
                        "content_type": "json",
                        "secret": webhook_secret,
                        "insecure_ssl": "0",
                    },
                },
            )
            if create_response.status_code in {200, 201}:
                actions_taken.append("Provisioned GitHub workflow_run webhook")
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="pass",
                        severity="info",
                        message="Webhook created successfully",
                    )
                )
            elif create_response.status_code == 422:
                actions_skipped.append("GitHub webhook creation skipped due to validation conflict")
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="warn",
                        severity="warning",
                        message="GitHub rejected webhook creation (likely already exists)",
                        remediation="Review existing hooks and ensure URL/event settings are correct",
                    )
                )
            elif create_response.status_code in {401, 403}:
                checks.append(_hooks_error_check(create_response.status_code))
                prompts.append("Grant webhook admin permissions to STITCH_GITHUB_TOKEN")
            else:
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="fail",
                        severity="error",
                        message=f"Unexpected webhook create response (HTTP {create_response.status_code})",
                        remediation="Retry later or create webhook manually",
                    )
                )
    except httpx.HTTPError as exc:
        checks.append(
            CheckResult(
                id="connect.webhook",
                status="fail",
                severity="error",
                message=f"Network/API error while provisioning webhook: {exc}",
                remediation="Check connectivity to GitHub API and retry",
            )
        )

    return build_command_report(
        command="connect",
        checks=checks,
        prompts=prompts,
        actions_taken=actions_taken,
        actions_skipped=actions_skipped,
        next_steps=[
            f"Run `stitch doctor --platform github --project-id {resolved_project_id} --json`"
        ],
    )


def _is_valid_webhook_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _split_project_id(project_id: str) -> tuple[str, str]:
    owner, repo = project_id.split("/", 1)
    return owner.strip(), repo.strip()


def _repo_error_check(*, status_code: int, project_id: str) -> CheckResult:
    if status_code in {401, 403}:
        return CheckResult(
            id="connectivity.repo",
            status="fail",
            severity="error",
            message=f"Token cannot access repository `{project_id}`",
            remediation="Grant repository access and `admin:repo_hook` permissions",
        )
    if status_code == 404:
        return CheckResult(
            id="connectivity.repo",
            status="fail",
            severity="error",
            message=f"Repository `{project_id}` not found",
            remediation="Verify `--project-id owner/repo` and retry",
        )
    return CheckResult(
        id="connectivity.repo",
        status="fail",
        severity="error",
        message=f"Repository check failed (HTTP {status_code})",
        remediation="Retry later or verify provider availability",
    )


def _hooks_error_check(status_code: int) -> CheckResult:
    if status_code in {401, 403}:
        return CheckResult(
            id="permissions.github_hooks",
            status="fail",
            severity="error",
            message="Token cannot manage repository webhooks",
            remediation="Grant webhook admin permissions (classic PAT: admin:repo_hook)",
        )
    return CheckResult(
        id="permissions.github_hooks",
        status="fail",
        severity="error",
        message=f"Webhook API check failed (HTTP {status_code})",
        remediation="Retry later or configure webhook manually",
    )


def _find_hook(hooks_payload: object, *, target_url: str) -> dict[str, object] | None:
    if not isinstance(hooks_payload, list):
        return None
    for hook in hooks_payload:
        if not isinstance(hook, dict):
            continue
        if hook.get("url") == target_url:
            return hook
        config = hook.get("config")
        if isinstance(config, dict) and config.get("url") == target_url:
            return hook
    return None


async def _run_gitlab_connect(
    *,
    checks: list[CheckResult],
    prompts: list[str],
    actions_taken: list[str],
    actions_skipped: list[str],
    repo_root: Path,
    project_id: str | None,
    webhook_url: str | None,
    settings: StitchSettings,
) -> CommandReport:
    gitlab_token = settings.gitlab_token
    webhook_secret = settings.webhook_secret
    gitlab_base_url = settings.gitlab_base_url

    if gitlab_token:
        checks.append(
            CheckResult(
                id="credentials.gitlab_token",
                status="pass",
                severity="info",
                message="STITCH_GITLAB_TOKEN is configured",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="credentials.gitlab_token",
                status="fail",
                severity="error",
                message="Missing STITCH_GITLAB_TOKEN",
                remediation="Export STITCH_GITLAB_TOKEN with `api` scope",
            )
        )
        prompts.append("Provide STITCH_GITLAB_TOKEN to continue automated connect")

    if webhook_secret:
        checks.append(
            CheckResult(
                id="credentials.webhook_secret",
                status="pass",
                severity="info",
                message="STITCH_WEBHOOK_SECRET is configured",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="credentials.webhook_secret",
                status="fail",
                severity="error",
                message="Missing STITCH_WEBHOOK_SECRET",
                remediation="Set STITCH_WEBHOOK_SECRET before provisioning provider webhooks",
            )
        )
        prompts.append("Provide STITCH_WEBHOOK_SECRET so webhook token can be configured")

    resolved_project_id = project_id or _detect_gitlab_project_id(repo_root)
    if resolved_project_id:
        checks.append(
            CheckResult(
                id="repo.project_id",
                status="pass",
                severity="info",
                message=f"Using GitLab project id: {resolved_project_id}",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="repo.project_id",
                status="fail",
                severity="error",
                message="Could not determine GitLab project id",
                remediation="Pass --project-id namespace/project or configure origin remote to GitLab",
            )
        )
        prompts.append("Provide --project-id in namespace/project format")

    if _is_valid_webhook_url(webhook_url):
        checks.append(
            CheckResult(
                id="input.webhook_url",
                status="pass",
                severity="info",
                message=f"Webhook target URL is valid: {webhook_url}",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="input.webhook_url",
                status="fail",
                severity="error",
                message="Missing or invalid webhook URL",
                remediation="Pass --webhook-url https://<public-host>/webhook/gitlab",
            )
        )
        prompts.append("Provide --webhook-url to the publicly reachable stitch webhook endpoint")

    if prompts:
        return build_command_report(command="connect", checks=checks, prompts=prompts)

    assert resolved_project_id is not None
    assert webhook_url is not None

    encoded_project_id = quote(resolved_project_id, safe="")
    base = gitlab_base_url.rstrip("/") + "/api/v4"
    project_url = f"{base}/projects/{encoded_project_id}"
    hooks_url = f"{base}/projects/{encoded_project_id}/hooks"
    headers = {"PRIVATE-TOKEN": gitlab_token}

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            project_response = await client.get(project_url, headers=headers)
            if project_response.status_code < 300:
                checks.append(
                    CheckResult(
                        id="connectivity.repo",
                        status="pass",
                        severity="info",
                        message=f"GitLab project is reachable: {resolved_project_id}",
                    )
                )
            else:
                checks.append(
                    _gitlab_project_error_check(
                        status_code=project_response.status_code,
                        project_id=resolved_project_id,
                    )
                )
                if project_response.status_code in {401, 403, 404}:
                    prompts.append(
                        "Adjust token permissions or project id and retry `stitch connect`"
                    )
                return build_command_report(command="connect", checks=checks, prompts=prompts)

            hooks_response = await client.get(hooks_url, headers=headers)
            if hooks_response.status_code >= 300:
                checks.append(_gitlab_hooks_error_check(hooks_response.status_code))
                if hooks_response.status_code in {401, 403}:
                    prompts.append(
                        "Grant maintainer access with `api` scope to STITCH_GITLAB_TOKEN"
                    )
                return build_command_report(command="connect", checks=checks, prompts=prompts)

            hooks_payload = hooks_response.json() if hooks_response.content else []
            existing = _find_hook(hooks_payload, target_url=webhook_url)
            if existing:
                actions_skipped.append("GitLab webhook already exists for target URL")
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="pass",
                        severity="info",
                        message="Webhook already provisioned",
                    )
                )
                return build_command_report(
                    command="connect",
                    checks=checks,
                    actions_skipped=actions_skipped,
                    next_steps=[
                        f"Run `stitch doctor --platform gitlab --project-id {resolved_project_id} --json`"
                    ],
                )

            create_response = await client.post(
                hooks_url,
                headers=headers,
                json={
                    "url": webhook_url,
                    "token": webhook_secret,
                    "pipeline_events": True,
                    "enable_ssl_verification": True,
                },
            )

            if create_response.status_code in {200, 201}:
                actions_taken.append("Provisioned GitLab pipeline webhook")
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="pass",
                        severity="info",
                        message="Webhook created successfully",
                    )
                )
            elif create_response.status_code in {409, 422}:
                actions_skipped.append("GitLab webhook creation skipped due to validation conflict")
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="warn",
                        severity="warning",
                        message="GitLab rejected webhook creation (likely already exists)",
                        remediation="Review existing hooks and ensure URL/event settings are correct",
                    )
                )
            elif create_response.status_code in {401, 403}:
                checks.append(_gitlab_hooks_error_check(create_response.status_code))
                prompts.append("Grant maintainer access with `api` scope to STITCH_GITLAB_TOKEN")
            else:
                checks.append(
                    CheckResult(
                        id="connect.webhook",
                        status="fail",
                        severity="error",
                        message=f"Unexpected webhook create response (HTTP {create_response.status_code})",
                        remediation="Retry later or create webhook manually",
                    )
                )
    except httpx.HTTPError as exc:
        checks.append(
            CheckResult(
                id="connect.webhook",
                status="fail",
                severity="error",
                message=f"Network/API error while provisioning webhook: {exc}",
                remediation="Check connectivity to GitLab API and retry",
            )
        )

    return build_command_report(
        command="connect",
        checks=checks,
        prompts=prompts,
        actions_taken=actions_taken,
        actions_skipped=actions_skipped,
        next_steps=[
            f"Run `stitch doctor --platform gitlab --project-id {resolved_project_id} --json`"
        ],
    )


def _detect_github_project_id(repo_root: Path) -> str | None:
    remote_url = _origin_remote_url(repo_root)
    if remote_url is None:
        return None
    return _parse_github_project_from_remote(remote_url)


def _detect_gitlab_project_id(repo_root: Path) -> str | None:
    remote_url = _origin_remote_url(repo_root)
    if remote_url is None:
        return None
    return _parse_gitlab_project_from_remote(remote_url)


def _origin_remote_url(repo_root: Path) -> str | None:
    git_config = repo_root / ".git" / "config"
    if not git_config.exists():
        return None

    raw = git_config.read_text()
    match = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(.+)', raw, re.MULTILINE | re.DOTALL)
    if not match:
        return None

    return match.group(1).strip().splitlines()[0].strip()


def _parse_github_project_from_remote(remote_url: str) -> str | None:
    patterns = [
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"^https?://(?:[^@/]+@)?github\.com/(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
    ]

    for pattern in patterns:
        matched = re.match(pattern, remote_url)
        if not matched:
            continue
        owner = matched.group("owner").strip()
        repo = matched.group("repo").strip()
        if owner and repo:
            return f"{owner}/{repo}"
    return None


def _parse_gitlab_project_from_remote(remote_url: str) -> str | None:
    patterns = [
        r"^git@[^:]+:(?P<path>.+?)(?:\.git)?$",
        r"^https?://(?:[^@/]+@)?[^/]+/(?P<path>.+?)(?:\.git)?$",
        r"^ssh://git@[^/]+/(?P<path>.+?)(?:\.git)?$",
    ]

    for pattern in patterns:
        matched = re.match(pattern, remote_url)
        if not matched:
            continue
        project_path = matched.group("path").strip().strip("/")
        if project_path and "/" in project_path:
            return project_path
    return None


def _gitlab_project_error_check(*, status_code: int, project_id: str) -> CheckResult:
    if status_code in {401, 403}:
        return CheckResult(
            id="connectivity.repo",
            status="fail",
            severity="error",
            message=f"Token cannot access project `{project_id}`",
            remediation="Grant project access and `api` scope to STITCH_GITLAB_TOKEN",
        )
    if status_code == 404:
        return CheckResult(
            id="connectivity.repo",
            status="fail",
            severity="error",
            message=f"Project `{project_id}` not found",
            remediation="Verify `--project-id namespace/project` and retry",
        )
    return CheckResult(
        id="connectivity.repo",
        status="fail",
        severity="error",
        message=f"Project check failed (HTTP {status_code})",
        remediation="Retry later or verify provider availability",
    )


def _gitlab_hooks_error_check(status_code: int) -> CheckResult:
    if status_code in {401, 403}:
        return CheckResult(
            id="permissions.gitlab_hooks",
            status="fail",
            severity="error",
            message="Token cannot manage project webhooks",
            remediation="Grant maintainer access with `api` scope to STITCH_GITLAB_TOKEN",
        )
    return CheckResult(
        id="permissions.gitlab_hooks",
        status="fail",
        severity="error",
        message=f"Webhook API check failed (HTTP {status_code})",
        remediation="Retry later or configure webhook manually",
    )
