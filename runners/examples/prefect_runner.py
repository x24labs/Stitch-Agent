"""Prefect flow that watches for failed pipelines and triggers stitch-agent.

Requirements:
    pip install stitch-agent[gitlab] prefect

Usage:
    prefect deploy runners/examples/prefect_runner.py:watch_gitlab_failures \
        --name stitch-watcher --interval 60
"""

from __future__ import annotations

import os

from prefect import flow, task

from stitch_agent import FixRequest, FixResult, StitchAgent
from stitch_agent.adapters.gitlab import GitLabAdapter
from stitch_agent.settings import StitchSettings


@task(retries=2, retry_delay_seconds=30)
async def fix_pipeline(
    project_id: str,
    pipeline_id: str,
    job_id: str,
    branch: str,
    job_name: str | None = None,
) -> FixResult:
    settings = StitchSettings()
    adapter = GitLabAdapter(
        token=settings.gitlab_token,
        base_url=settings.gitlab_base_url,
    )
    agent = StitchAgent(adapter=adapter, anthropic_api_key=settings.anthropic_api_key)
    request = FixRequest(
        platform="gitlab",
        project_id=project_id,
        pipeline_id=pipeline_id,
        job_id=job_id,
        branch=branch,
        job_name=job_name,
    )
    async with adapter:
        return await agent.fix(request)


@flow(name="stitch-gitlab-watcher", log_prints=True)
async def watch_gitlab_failures(
    project_ids: list[str] | None = None,
    dry_run: bool = False,
) -> list[FixResult]:
    """Poll GitLab projects for failed pipelines and auto-fix them."""
    import httpx

    settings = StitchSettings()
    projects = project_ids or os.environ.get("STITCH_PROJECT_IDS", "").split(",")
    projects = [p.strip() for p in projects if p.strip()]

    results: list[FixResult] = []
    headers = {"PRIVATE-TOKEN": settings.gitlab_token}

    async with httpx.AsyncClient(base_url=settings.gitlab_base_url) as client:
        for project_id in projects:
            resp = await client.get(
                f"/api/v4/projects/{project_id}/pipelines",
                params={"status": "failed", "per_page": 5},
                headers=headers,
            )
            resp.raise_for_status()
            pipelines = resp.json()

            for pipeline in pipelines:
                jobs_resp = await client.get(
                    f"/api/v4/projects/{project_id}/pipelines/{pipeline['id']}/jobs",
                    params={"scope": "failed"},
                    headers=headers,
                )
                jobs_resp.raise_for_status()
                failed_jobs = jobs_resp.json()

                if not failed_jobs:
                    continue

                job = failed_jobs[0]
                print(
                    f"Fixing {project_id} pipeline={pipeline['id']} "
                    f"job={job['name']} branch={pipeline['ref']}"
                )

                if not dry_run:
                    result = await fix_pipeline(
                        project_id=str(project_id),
                        pipeline_id=str(pipeline["id"]),
                        job_id=str(job["id"]),
                        branch=pipeline["ref"],
                        job_name=job["name"],
                    )
                    print(f"  → {result.status} ({result.error_type})")
                    results.append(result)

    return results


if __name__ == "__main__":
    import asyncio

    asyncio.run(watch_gitlab_failures())
