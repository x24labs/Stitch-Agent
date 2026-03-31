"""CI-native runner — runs stitch as a CI job with zero external infrastructure.

Detects platform from environment variables and processes failed jobs automatically.

Modes on stitch/fix-* branches (auto-detected):
  1. FIX mode    — CI failed on a normal branch → generate fix, push stitch/fix-* branch
  2. VERIFY mode — CI passed on stitch/fix-* → create MR
  3. RETRY mode  — CI failed on stitch/fix-* → retry fix with model escalation

Both GitLab (.post stage) and GitHub (workflow_run) discover failed jobs via API.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from importlib.metadata import version as pkg_version
from typing import Literal

from stitch_agent.core.agent import StitchAgent
from stitch_agent.models import FixRequest
from stitch_agent.settings import StitchSettings


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[stitch] %(levelname)s %(message)s"))
    logger = logging.getLogger("stitch_agent")
    logger.setLevel(level)
    logger.addHandler(handler)


def _get_version() -> str:
    try:
        return pkg_version("stitch-agent")
    except Exception:
        return "dev"


def _print_banner(ctx: CIContext) -> None:
    ver = _get_version()
    mode = "RETRY" if _is_stitch_branch(ctx.branch) else "FIX"
    branch = ctx.branch
    pipeline = ctx.pipeline_id

    print(
        f"\n"
        f"  ┌─────────────────────────────────────────┐\n"
        f"  │  Stitch Agent v{ver:<25s}│\n"
        f"  │  The AI that stitches your CI back      │\n"
        f"  │  together.                              │\n"
        f"  ├─────────────────────────────────────────┤\n"
        f"  │  Platform:  {ctx.platform:<28s}│\n"
        f"  │  Mode:      {mode:<28s}│\n"
        f"  │  Branch:    {branch[:28]:<28s}│\n"
        f"  │  Pipeline:  {pipeline[:28]:<28s}│\n"
        f"  └─────────────────────────────────────────┘\n",
        file=sys.stderr,
        flush=True,
    )

_STITCH_BRANCH_RE = re.compile(r"^stitch/fix-")
_TARGET_RE = re.compile(r"^Stitch-Target:\s*(.+)$", re.M)


@dataclass
class CIContext:
    platform: Literal["gitlab", "github"]
    project_id: str
    pipeline_id: str
    branch: str
    base_url: str | None = None
    job_id: str | None = None
    job_name: str | None = None
    # commit message from CI env (avoids API call)
    commit_message: str | None = None


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
    commit_message = os.environ.get("CI_COMMIT_MESSAGE")

    return CIContext(
        platform="gitlab",
        project_id=project_id,
        pipeline_id=pipeline_id,
        branch=branch,
        base_url=base_url or None,
        commit_message=commit_message,
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


def _extract_target_branch(commit_msg: str) -> str | None:
    match = _TARGET_RE.search(commit_msg)
    return match.group(1).strip() if match else None


async def _run_stitch_branch_mode(
    ctx: CIContext,
    platform: Literal["gitlab", "github"],
    settings: StitchSettings,
    output_format: str,
) -> int:
    """Handle stitch/fix-* branches: check for failures → verify or escalate."""
    adapter = _build_adapter(platform, ctx, settings)

    async with adapter:
        # Get target branch from commit metadata (prefer CI env var, fall back to API)
        commit_msg = ctx.commit_message or ""
        if not _extract_target_branch(commit_msg):
            with contextlib.suppress(Exception):
                commit_msg = await adapter.get_latest_commit_message(ctx.project_id, ctx.branch)
        target_branch = _extract_target_branch(commit_msg)
        if not target_branch:
            msg = (
                f"Cannot determine target branch from commit on {ctx.branch}. "
                f"Expected 'Stitch-Target: <branch>' trailer in commit message."
            )
            if output_format == "json":
                print(json.dumps({"status": "error", "reason": msg}))
            else:
                print(f"\u274c {msg}", file=sys.stderr)
            return 1

        # Check if there are failed jobs in this pipeline (excluding stitch jobs)
        failed_jobs = await adapter.list_failed_jobs(ctx.project_id, ctx.pipeline_id)
        non_stitch_failures = [
            j for j in failed_jobs
            if not str(j.get("name", "")).startswith("stitch")
        ]

        if non_stitch_failures:
            return await _handle_fix_failed(
                ctx, platform, adapter, settings,
                target_branch, non_stitch_failures, output_format,
            )

        return await _handle_fix_verified(
            ctx, platform, adapter, target_branch, commit_msg, output_format
        )


async def _handle_fix_verified(
    ctx: CIContext,
    platform: Literal["gitlab", "github"],
    adapter,
    target_branch: str,
    commit_msg: str,
    output_format: str,
) -> int:
    """CI passed on fix branch — create MR."""
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
        f"[stitch-agent](https://git.g24r.com/x24labs/stitch/library).*"
    )

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


async def _handle_fix_failed(
    ctx: CIContext,
    platform: Literal["gitlab", "github"],
    adapter,
    settings: StitchSettings,
    target_branch: str,
    failed_jobs: list[dict],
    output_format: str,
) -> int:
    """CI failed on fix branch — retry automatically with model escalation.

    Counts commits on the fix branch to track attempts. Escalates to
    a stronger model after initial retries. Only escalates to human
    review after all attempts are exhausted.
    """
    job_names = [str(j.get("name", j.get("id", "?"))) for j in failed_jobs]

    attempt = await adapter.count_branch_commits(
        ctx.project_id, ctx.branch, target_branch
    )
    max_retry = settings.max_attempts

    if attempt >= max_retry:
        result = {
            "status": "fix_exhausted",
            "fix_branch": ctx.branch,
            "target_branch": target_branch,
            "attempts": attempt,
            "failed_jobs": job_names,
            "reason": (
                f"Exhausted {attempt} fix attempts on {ctx.branch}. "
                f"Failed jobs: {', '.join(job_names)}. Human review needed."
            ),
        }
        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\u274c [exhausted] {attempt} attempts on {ctx.branch}, human review needed")
            print(f"   Target: {target_branch}")
            print(f"   Failed jobs: {', '.join(job_names)}")
        return 1

    job = failed_jobs[0]
    job_id = str(job.get("id", ""))
    model_hint = "sonnet" if attempt >= 2 else "auto"

    if output_format != "json":
        print(
            f"\U0001f504 [retry] Attempt {attempt + 1}/{max_retry} "
            f"on {ctx.branch} (model: {model_hint})"
        )
        print(f"   Fixing: {job_names[0]}")

    agent = StitchAgent(
        adapter=adapter,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        haiku_confidence_threshold=settings.haiku_confidence_threshold,
        sonnet_confidence_threshold=settings.sonnet_confidence_threshold,
        max_attempts=max_retry,
    )

    request = FixRequest(
        platform=platform,
        project_id=ctx.project_id,
        pipeline_id=ctx.pipeline_id,
        job_id=job_id,
        branch=ctx.branch,
        job_name=str(job.get("name", "")),
    )

    try:
        fix_result = await agent.retry_fix(
            request=request,
            fix_branch=ctx.branch,
            target_branch=target_branch,
            attempt=attempt,
        )
    except Exception as exc:
        if output_format == "json":
            print(json.dumps({"status": "retry_error", "reason": str(exc)}, indent=2))
        else:
            print(f"\u274c [retry_error] {exc}")
        return 1

    if fix_result.status == "fixed":
        if output_format == "json":
            print(json.dumps({
                "status": "retried",
                "fix_branch": ctx.branch,
                "attempt": attempt + 1,
                "reason": fix_result.reason,
            }, indent=2))
        else:
            print(f"\u2705 [retried] Pushed retry fix to {ctx.branch}")
            print(f"   {fix_result.reason}")
        return 0

    if output_format == "json":
        print(json.dumps({"status": "retry_failed", "reason": fix_result.reason}, indent=2))
    else:
        print(f"\u26a0\ufe0f [retry_failed] {fix_result.reason}")
    return 1


def _error_signature(log: str) -> str:
    """Extract a short signature from a job log for deduplication.

    Looks for the core error line (e.g. "unrecognized arguments: --cov-data-file")
    stripping variable parts like file names, timestamps, and coverage module names.
    """
    # Generic lines that don't identify the actual error
    _SKIP = ("section_", "Cleaning", "Uploading", "WARNING", "Job failed", "exit code")

    for line in reversed(log.splitlines()):
        line = line.strip()
        if not line:
            continue
        # Strip GitLab timestamp prefix (e.g. "2026-03-28T06:34:08.247629Z 01E ")
        line = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+\w+\s*", "", line)
        line = line.strip()
        if not line or any(line.startswith(s) or s in line for s in _SKIP):
            continue
        if "error" in line.lower() or "failed" in line.lower():
            # Normalize variable parts
            sig = re.sub(r"\.coverage\.\w+", ".coverage.*", line)
            sig = re.sub(r":\d+:\d+", ":*:*", sig)
            sig = re.sub(r"tests/\S+\.py::\S+", "tests/*", sig)
            return sig[:200]
    return log[-200:]


async def _group_by_error(
    adapter, ctx: CIContext, jobs: list[dict[str, str]],
) -> list[tuple[dict[str, str], list[dict[str, str]]]]:
    """Group failed jobs by error signature.

    Returns a list of (representative_job, [all_jobs_in_group]) tuples.
    Jobs with the same error signature are grouped together so we only
    fix each unique root cause once.
    """
    signatures: dict[str, list[dict[str, str]]] = {}
    for job in jobs:
        try:
            request = FixRequest(
                platform=ctx.platform,
                project_id=ctx.project_id,
                pipeline_id=ctx.pipeline_id,
                job_id=job["id"],
                branch=ctx.branch,
            )
            log = await adapter.fetch_job_logs(request)
            sig = _error_signature(log)
        except Exception:
            sig = f"__unique_{job['id']}"
        signatures.setdefault(sig, []).append(job)

    return [(group[0], group) for group in signatures.values()]


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
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        haiku_confidence_threshold=settings.haiku_confidence_threshold,
        sonnet_confidence_threshold=settings.sonnet_confidence_threshold,
        max_attempts=settings.max_attempts,
    )

    jobs_to_fix: list[dict[str, str]] = []
    results: list[dict[str, object]] = []

    async with adapter:
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

        # Deduplicate: group jobs by error signature so we only fix each
        # unique root cause once instead of N times for the same error.
        groups = await _group_by_error(adapter, ctx, jobs_to_fix)

        for representative, group_jobs in groups:
            job_names_in_group = [j["name"] or j["id"] for j in group_jobs]
            if len(group_jobs) > 1:
                print(
                    f"Grouped {len(group_jobs)} jobs with same error: "
                    f"{', '.join(job_names_in_group)}",
                    file=sys.stderr,
                )

            request = FixRequest(
                platform=platform,
                project_id=ctx.project_id,
                pipeline_id=ctx.pipeline_id,
                job_id=representative["id"],
                branch=ctx.branch,
                job_name=representative["name"] or None,
            )
            try:
                result = await agent.fix(request, create_mr=False)
                result_dict = result.model_dump()
                # Report all jobs in the group as having the same result
                for job in group_jobs:
                    results.append({"job_id": job["id"], "job_name": job["name"], **result_dict})
            except Exception as exc:
                for job in group_jobs:
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
    verbose: bool = False,
) -> int:
    _setup_logging(verbose or os.environ.get("STITCH_VERBOSE", "") == "1")
    platform = detect_platform(platform_override)
    ctx = build_context(platform)
    settings = StitchSettings()

    _print_banner(ctx)

    if _is_stitch_branch(ctx.branch):
        return await _run_stitch_branch_mode(ctx, platform, settings, output_format)

    return await _run_fix_mode(ctx, platform, settings, output_format, max_jobs)


def _print_text_results(results: list[dict[str, object]]) -> None:
    icons = {"fixed": "\u2705", "escalate": "\u26a0\ufe0f", "error": "\u274c"}
    for r in results:
        status = str(r.get("status", "error"))
        icon = icons.get(status, "?")
        job_label = r.get("job_name") or r.get("job_id", "?")
        print(f"{icon} [{job_label}] {status.capitalize()}")
        if r.get("reason"):
            print(f"   Reason: {r['reason']}")
        if r.get("fix_branch"):
            print(f"   Branch: {r['fix_branch']}")
        if r.get("mr_url"):
            print(f"   MR URL: {r['mr_url']}")
        usage = r.get("usage")
        if usage and isinstance(usage, dict) and usage.get("total_tokens"):
            cost = usage.get("cost_usd", 0)
            cost_str = f" — ${cost:.4f}" if cost else ""
            print(f"   Tokens: {usage['prompt_tokens']:,} in / {usage['completion_tokens']:,} out ({usage['total_tokens']:,} total){cost_str}")
