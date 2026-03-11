"""Dagster job that reacts to failed CI pipelines via a sensor.

Requirements:
    pip install stitch-agent[github] dagster dagster-webserver

Usage:
    dagster dev -f runners/examples/dagster_runner.py
"""

from __future__ import annotations

from dagster import (
    Definitions,
    OpExecutionContext,
    RunRequest,
    SensorEvaluationContext,
    job,
    op,
    sensor,
)

from stitch_agent import FixRequest, FixResult, StitchAgent
from stitch_agent.adapters.github import GitHubAdapter
from stitch_agent.settings import StitchSettings


@op
async def stitch_fix_op(context: OpExecutionContext) -> dict:
    project_id: str = context.op_config["project_id"]
    pipeline_id: str = context.op_config["pipeline_id"]
    job_id: str = context.op_config["job_id"]
    branch: str = context.op_config["branch"]

    settings = StitchSettings()
    adapter = GitHubAdapter(token=settings.github_token)
    agent = StitchAgent(adapter=adapter, anthropic_api_key=settings.anthropic_api_key)
    request = FixRequest(
        platform="github",
        project_id=project_id,
        pipeline_id=pipeline_id,
        job_id=job_id,
        branch=branch,
    )
    async with adapter:
        result: FixResult = await agent.fix(request)

    context.log.info(f"stitch result: {result.status} ({result.error_type})")
    return result.model_dump()


@job
def stitch_fix_job() -> None:
    stitch_fix_op()  # type: ignore[call-arg]


@sensor(job=stitch_fix_job, minimum_interval_seconds=60)
def github_failure_sensor(context: SensorEvaluationContext):
    import os

    import httpx

    settings = StitchSettings()
    project_ids_env = os.environ.get("STITCH_PROJECT_IDS", "")
    project_ids = [p.strip() for p in project_ids_env.split(",") if p.strip()]

    for project_id in project_ids:
        with httpx.Client(base_url="https://api.github.com") as client:
            resp = client.get(
                f"/repos/{project_id}/actions/runs",
                headers={"Authorization": f"Bearer {settings.github_token}"},
                params={"status": "failure", "per_page": 3},
            )
        if resp.status_code != 200:
            continue
        runs = resp.json().get("workflow_runs", [])
        for run in runs:
            run_id = str(run["id"])
            if context.cursor and run_id <= context.cursor:
                continue
            yield RunRequest(
                run_key=f"{project_id}-{run_id}",
                run_config={
                    "ops": {
                        "stitch_fix_op": {
                            "config": {
                                "project_id": project_id,
                                "pipeline_id": run_id,
                                "job_id": run.get("head_sha", ""),
                                "branch": run.get("head_branch", "main"),
                            }
                        }
                    }
                },
            )
        if runs:
            context.update_cursor(str(runs[0]["id"]))


defs = Definitions(jobs=[stitch_fix_job], sensors=[github_failure_sensor])
