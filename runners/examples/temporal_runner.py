"""Temporal worker that runs stitch-agent as a workflow.

Requirements:
    pip install stitch-agent[github] temporalio

Usage:
    python runners/examples/temporal_runner.py worker  # start the worker
    python runners/examples/temporal_runner.py run <project> <run_id> <sha> <branch>
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

from stitch_agent import FixRequest, FixResult, StitchAgent
from stitch_agent.adapters.github import GitHubAdapter
from stitch_agent.settings import StitchSettings


@dataclass
class StitchInput:
    project_id: str
    pipeline_id: str
    job_id: str
    branch: str


TASK_QUEUE = "stitch-agent"


@activity.defn
async def run_stitch_fix(inp: StitchInput) -> dict:
    settings = StitchSettings()
    adapter = GitHubAdapter(token=settings.github_token)
    agent = StitchAgent(adapter=adapter, anthropic_api_key=settings.anthropic_api_key)
    request = FixRequest(
        platform="github",
        project_id=inp.project_id,
        pipeline_id=inp.pipeline_id,
        job_id=inp.job_id,
        branch=inp.branch,
    )
    async with adapter:
        result: FixResult = await agent.fix(request)
    return result.model_dump()


@workflow.defn
class StitchWorkflow:
    @workflow.run
    async def run(self, inp: StitchInput) -> dict:
        return await workflow.execute_activity(
            run_stitch_fix,
            inp,
            schedule_to_close_timeout=__import__("datetime").timedelta(minutes=10),
        )


async def _start_worker() -> None:
    client = await Client.connect(
        "localhost:7233",
        namespace=__import__("os").environ.get("TEMPORAL_NAMESPACE", "default"),
    )
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[StitchWorkflow],
        activities=[run_stitch_fix],
    )
    print(f"Worker listening on task queue: {TASK_QUEUE}")
    await worker.run()


async def _trigger(project_id: str, pipeline_id: str, job_id: str, branch: str) -> dict:
    client = await Client.connect("localhost:7233")
    result = await client.execute_workflow(
        StitchWorkflow.run,
        StitchInput(
            project_id=project_id,
            pipeline_id=pipeline_id,
            job_id=job_id,
            branch=branch,
        ),
        id=f"stitch-{pipeline_id}",
        task_queue=TASK_QUEUE,
    )
    return result


if __name__ == "__main__":
    import asyncio

    if len(sys.argv) < 2 or sys.argv[1] == "worker":
        asyncio.run(_start_worker())
    elif sys.argv[1] == "run" and len(sys.argv) == 6:
        result = asyncio.run(_trigger(*sys.argv[2:]))
        print(result)
    else:
        print(__doc__)
        sys.exit(1)
