"""CI-native runner — runs stitch as a CI job with zero external infrastructure.

Detects platform from environment variables and processes failed jobs automatically.

Two-phase flow:
  1. FIX mode  — CI failed on a normal branch → generate fix, create stitch/fix-* branch (no MR)
  2. VERIFY mode — CI passed on a stitch/fix-* branch → create MR

GitLab modes (fix phase):
  - after_script: CI_JOB_STATUS present, job_id is the failed job itself
  - .post stage:  CI_JOB_STATUS absent, discovers failed jobs via API

GitHub:
  - Always uses GITHUB_EVENT_PATH (workflow_run event payload)
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Literal

from stitch_agent.core.agent import StitchAgent
from stitch_agent.models import FixRequest
from stitch_agent.settings import StitchSettings

_STITCH_BRANCH_RE = re.compile(r"^stitch/fix-")
_TARGET_RE = re.compile(r"^Stitch-Target:\s*(.+)$", re.M)


@dataclass
class CIContext:
    platform: Literal["gitlab", "github"]
    project_id: str
    pipeline_id: str
    branch: str
    base_url: str | None = None
    # after_script mode: single job already known
    job_id: str | None = None
    job_name: str | None = None


def _is_stitch_branch(branch: str) -> bool:
    return bool(_STITCH_BRANCH_RE.match(branch))


def detect_platform(override: str | None = None) -> Literal["gitlab", "github"]:
    if override:
        if override not in ("gitlab", "github"):
            raise SystemExit(f"Unknown platform: {override}")
        return override  # type: ignore[return-value]
    if os.environ.get("CI_PROJECT_ID"):
        return "gitlab"
    if os.environ.get("GITHUB_REPOSITORY"):
        return "github"
    raise SystemExit(
        "Cannot detect CI platform. Expected CI_PROJECT_ID (GitLab) or "
        "GITHUB_REPOSITORY (GitHub) in environment. "
        "Use --platform to override auto-detection."
    )


def _build_gitlab_context() -> CIContext:
    project_id = os.environ["CI_PROJECT_ID"]
    pipeline_id = os.environ.get("CI_PIPELINE_ID", "")
    branch = os.environ.get("CI_COMMIT_REF_NAME", "")
    base_url = os.environ.get("CI_SERVER_URL")

    job_status = os.environ.get("CI_JOB_STATUS")
    if job_status == "failed":
        # after_script mode: this job is the failed one
        return CIContext(
            platform="gitlab",
            project_id=project_id,
            pipeline_id=pipeline_id,
            branch=branch,
            base_url=f"{base_url}" if base_url else None,
            job_id=os.environ.get("CI_JOB_ID"),
            job_name=os.environ.get("CI_JOB_NAME"),
        )

    # .post stage mode: need to discover failed jobs
    return CIContext(
        platform="gitlab",
        project_id=project_id,
        pipeline_id=pipeline_id,
        branch=branch,
        base_url=f"{base_url}" if base_url else None,
    )


def _build_github_context() -> CIContext:
    project_id = os.environ["GITHUB_REPOSITORY"]
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    base_url = os.environ.get("GITHUB_API_URL")

    if not event_path:
        raise SystemExit("GITHUB_EVENT_PATH not set — is this running inside GitHub Actions?")

    with open(event_path) as f:
        event = json.load(f)

    workflow_run = event.get("workflow_run", {})
    pipeline_id = str(workflow_run.get("id", ""))
    branch = workflow_run.get("head_branch", os.environ.get("GITHUB_REF_NAME", ""))

    if not pipeline_id:
        raise SystemExit(
            "No workflow_run.id in event payload. "
            "Ensure this workflow is triggered by workflow_run events."
        )

    return CIContext(
        platform="github",
        project_id=project_id,
        pipeline_id=pipeline_id,
        branch=branch,
        base_url=base_url,
    )


def build_context(platform: Literal["gitlab", "github"]) -> CIContext:
    if platform == "gitlab":
        return _build_gitlab_context()
    return _build_github_context()


def _build_adapter(platform: Literal["gitlab", "github"], ctx: CIContext, settings: StitchSettings):
    if platform == "gitlab":
        from stitch_agent.adapters.gitlab import GitLabAdapter

        base_url = ctx.base_url or settings.gitlab_base_url
        return GitLabAdapter(token=settings.gitlab_token, base_url=base_url)

    from stitch_agent.adapters.github import GitHubAdapter

    base_url = ctx.base_url or settings.github_base_url
    return GitHubAdapter(token=settings.github_token, base_url=base_url)


async def _run_verify_mode(
    ctx: CIContext,
    platform: Literal["gitlab", "github"],
    settings: StitchSettings,
    output_format: str,
) -> int:
    """CI passed on a stitch/fix-* branch — extract target branch and create MR."""
    adapter = _build_adapter(platform, ctx, settings)

    async with adapter:
        commit_msg = await adapter.get_latest_commit_message(ctx.project_id, ctx.branch)
        target_match = _TARGET_RE.search(commit_msg)
        if not target_match:
            msg = (
                f"Cannot determine target branch from commit on {ctx.branch}. "
                f"Expected 'Stitch-Target: <branch>' trailer in commit message."
            )
            if output_format == "json":
                print(json.dumps({"status": "error", "reason": msg}))
            else:
                print(f"\u274c {msg}", file=sys.stderr)
            return 1

        target_branch = target_match.group(1).strip()

        # Build MR description from commit message
        first_line = commit_msg.split("\n", 1)[0]
        title = f"stitch: {first_line}" if not first_line.startswith("stitch:") else first_line
        description = (
            f"## Automated fix by stitch\n\n"
            f"**Fix branch:** `{ctx.branch}`\n"
            f"**Target branch:** `{target_branch}`\n\n"
            f"### Commit message\n```\n{commit_msg.strip()}\n```\n\n"
            f"CI passed on the fix branch — this fix has been verified.\n\n"
            f"---\n"
            f"*This MR was created automatically by "
            f"[stitch-agent](https://github.com/g24r/stitch).*"
        )

        # Create a FixRequest pointing to the target branch
        request = FixRequest(
            platform=platform,
            project_id=ctx.project_id,
            pipeline_id=ctx.pipeline_id,
            job_id="0",
            branch=target_branch,
        )

        mr_url = await adapter.create_merge_request(
            request=request,
            fix_branch=ctx.branch,
            title=title,
            description=description,
        )

    result = {
        "status": "verified",
        "fix_branch": ctx.branch,
        "target_branch": target_branch,
        "mr_url": mr_url,
    }

    if output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"\u2705 [verify] CI passed on {ctx.branch}")
        print(f"   Target: {target_branch}")
        print(f"   MR URL: {mr_url}")

    return 0


async def _run_fix_mode(
    ctx: CIContext,
    platform: Literal["gitlab", "github"],
    settings: StitchSettings,
    output_format: str,
    max_jobs: int,
) -> int:
    """CI failed on a normal branch — generate fix and create stitch/fix-* branch (no MR)."""
    adapter = _build_adapter(platform, ctx, settings)

    agent = StitchAgent(
        adapter=adapter,
        anthropic_api_key=settings.anthropic_api_key,
        haiku_confidence_threshold=settings.haiku_confidence_threshold,
        sonnet_confidence_threshold=settings.sonnet_confidence_threshold,
        max_attempts=settings.max_attempts,
    )

    jobs_to_fix: list[dict[str, str]] = []
    results: list[dict[str, object]] = []

    async with adapter:
        if ctx.job_id:
            jobs_to_fix = [{"id": ctx.job_id, "name": ctx.job_name or ""}]
        else:
            discovered = await adapter.list_failed_jobs(ctx.project_id, ctx.pipeline_id)
            jobs_to_fix = [{"id": str(j["id"]), "name": str(j.get("name", ""))} for j in discovered]

        if not jobs_to_fix:
            if output_format == "json":
                print(json.dumps({"status": "no_failures", "jobs": []}))
            else:
                print("No failed jobs found in pipeline.")
            return 0

        if len(jobs_to_fix) > max_jobs:
            print(
                f"Found {len(jobs_to_fix)} failed jobs, processing first {max_jobs} "
                f"(use --max-jobs to adjust).",
                file=sys.stderr,
            )
            jobs_to_fix = jobs_to_fix[:max_jobs]

        for job in jobs_to_fix:
            request = FixRequest(
                platform=platform,
                project_id=ctx.project_id,
                pipeline_id=ctx.pipeline_id,
                job_id=job["id"],
                branch=ctx.branch,
                job_name=job["name"] or None,
            )
            try:
                result = await agent.fix(request, create_mr=False)
                results.append({"job_id": job["id"], "job_name": job["name"], **result.model_dump()})
            except Exception as exc:
                results.append({
                    "job_id": job["id"],
                    "job_name": job["name"],
                    "status": "error",
                    "reason": str(exc),
                })

    if output_format == "json":
        print(json.dumps({"status": "complete", "jobs": results}, indent=2))
    else:
        _print_text_results(results)

    return 0 if all(r.get("status") == "fixed" for r in results) else 1


async def run_ci(
    output_format: str = "text",
    platform_override: str | None = None,
    max_jobs: int = 5,
) -> int:
    platform = detect_platform(platform_override)
    ctx = build_context(platform)
    settings = StitchSettings()

    if _is_stitch_branch(ctx.branch):
        return await _run_verify_mode(ctx, platform, settings, output_format)

    return await _run_fix_mode(ctx, platform, settings, output_format, max_jobs)


def _print_text_results(results: list[dict[str, object]]) -> None:
    icons = {"fixed": "\u2705", "escalate": "\u26a0\ufe0f", "error": "\u274c"}
    for r in results:
        status = str(r.get("status", "error"))
        icon = icons.get(status, "?")
        job_label = r.get("job_name") or r.get("job_id", "?")
        print(f"{icon} [{job_label}] {status}")
        if r.get("reason"):
            print(f"   Reason: {r['reason']}")
        if r.get("fix_branch"):
            print(f"   Branch: {r['fix_branch']}")
        if r.get("mr_url"):
            print(f"   MR URL: {r['mr_url']}")
