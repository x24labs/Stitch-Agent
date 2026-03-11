from __future__ import annotations

import hashlib
import hmac
import logging
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from stitch_agent.adapters.gitlab import GitLabAdapter
from stitch_agent.core.agent import StitchAgent
from stitch_agent.models import FixRequest
from stitch_agent.settings import StitchSettings

logger = logging.getLogger("stitch.webhook")
app = FastAPI(title="stitch-agent webhook", version="0.1.0")
_settings = StitchSettings()

_rate_buckets: dict[str, deque[datetime]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(client_ip: str) -> bool:
    now = datetime.now(UTC)
    window_start = now - timedelta(seconds=_settings.webhook_rate_window)
    bucket = _rate_buckets[client_ip]
    while bucket and bucket[0] < window_start:
        bucket.popleft()
    if len(bucket) >= _settings.webhook_rate_limit:
        return False
    bucket.append(now)
    return True


def _verify_api_key(request: Request) -> bool:
    if not _settings.webhook_api_keys:
        return True
    allowed = {k.strip() for k in _settings.webhook_api_keys.split(",") if k.strip()}
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] in allowed
    return False


def _enforce_auth(request: Request) -> None:
    if not _verify_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    client_ip = _client_ip(request)
    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {_settings.webhook_rate_limit} req/{_settings.webhook_rate_window}s",
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "stitch-agent"}


@app.post("/webhook/gitlab")
async def gitlab_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    _enforce_auth(request)

    if _settings.webhook_secret:
        token = request.headers.get("X-Gitlab-Token", "")
        if token != _settings.webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook token")

    payload = await request.json()
    if payload.get("object_kind") != "pipeline":
        return JSONResponse({"status": "ignored", "reason": "not a pipeline event"})

    attributes = payload.get("object_attributes", {})
    if attributes.get("status") != "failed":
        return JSONResponse(
            {"status": "ignored", "reason": f"pipeline status is {attributes.get('status')}"}
        )

    builds = payload.get("builds", [])
    failed_jobs = [b for b in builds if b.get("status") == "failed"]
    if not failed_jobs:
        return JSONResponse({"status": "ignored", "reason": "no failed jobs found"})

    project = payload.get("project", {})
    pipeline_id = str(attributes.get("id", ""))
    branch = attributes.get("ref", "")

    queued = 0
    for job in failed_jobs:
        fix_request = FixRequest(
            platform="gitlab",
            project_id=str(project.get("id", "")),
            pipeline_id=pipeline_id,
            job_id=str(job.get("id", "")),
            branch=branch,
            job_name=job.get("name"),
        )
        background_tasks.add_task(_run_gitlab_fix, fix_request)
        queued += 1

    return JSONResponse({"status": "accepted", "jobs_queued": queued})


@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    _enforce_auth(request)

    body = await request.body()

    if _settings.webhook_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = (
            "sha256="
            + hmac.new(
                _settings.webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Invalid HMAC signature")

    payload = await request.json()
    event = request.headers.get("X-GitHub-Event", "")

    if event != "workflow_run":
        return JSONResponse(
            {"status": "ignored", "reason": f"not a workflow_run event (got {event})"}
        )

    workflow_run = payload.get("workflow_run", {})
    action = payload.get("action", "")

    if action != "completed" or workflow_run.get("conclusion") != "failure":
        return JSONResponse({"status": "ignored", "reason": "workflow not failed"})

    repo = payload.get("repository", {})
    project_id = repo.get("full_name", "")
    run_id = str(workflow_run.get("id", ""))
    branch = workflow_run.get("head_branch", "")
    head_sha = workflow_run.get("head_sha", "")

    fix_request = FixRequest(
        platform="github",
        project_id=project_id,
        pipeline_id=run_id,
        job_id=head_sha,
        branch=branch,
    )
    background_tasks.add_task(_run_github_fix, fix_request)
    return JSONResponse({"status": "accepted", "run_id": run_id})


async def _run_gitlab_fix(fix_request: FixRequest) -> None:
    logger.info(
        "Starting GitLab fix pipeline=%s branch=%s", fix_request.pipeline_id, fix_request.branch
    )
    adapter = GitLabAdapter(token=_settings.gitlab_token, base_url=_settings.gitlab_base_url)
    agent = StitchAgent(
        adapter=adapter,
        anthropic_api_key=_settings.anthropic_api_key,
        haiku_confidence_threshold=_settings.haiku_confidence_threshold,
        sonnet_confidence_threshold=_settings.sonnet_confidence_threshold,
        validation_mode=_settings.validation_mode,
        max_attempts=_settings.max_attempts,
    )
    try:
        async with adapter:
            result = await agent.fix(fix_request)
        logger.info(
            "Fix done: status=%s type=%s mr=%s",
            result.status,
            result.error_type.value,
            result.mr_url,
        )
    except Exception:
        logger.exception("Fix failed for pipeline=%s", fix_request.pipeline_id)


async def _run_github_fix(fix_request: FixRequest) -> None:
    from stitch_agent.adapters.github import GitHubAdapter

    logger.info("Starting GitHub fix run=%s branch=%s", fix_request.pipeline_id, fix_request.branch)
    adapter = GitHubAdapter(token=_settings.github_token, base_url=_settings.github_base_url)
    agent = StitchAgent(
        adapter=adapter,
        anthropic_api_key=_settings.anthropic_api_key,
        haiku_confidence_threshold=_settings.haiku_confidence_threshold,
        sonnet_confidence_threshold=_settings.sonnet_confidence_threshold,
        validation_mode=_settings.validation_mode,
        max_attempts=_settings.max_attempts,
    )
    try:
        async with adapter:
            result = await agent.fix(fix_request)
        logger.info(
            "Fix done: status=%s type=%s mr=%s",
            result.status,
            result.error_type.value,
            result.mr_url,
        )
    except Exception:
        logger.exception("Fix failed for run=%s", fix_request.pipeline_id)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=_settings.webhook_host, port=_settings.webhook_port)


if __name__ == "__main__":
    main()
