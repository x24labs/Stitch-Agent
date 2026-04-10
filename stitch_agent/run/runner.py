"""Runner loop — orchestrate local CI execution with an AI fix loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stitch_agent.run.executor import LocalExecutor
from stitch_agent.run.models import (
    CIJob,
    FixContext,
    JobResult,
    RunReport,
)

if TYPE_CHECKING:
    from pathlib import Path

    from stitch_agent.run.drivers.base import AgentDriver

_ERROR_LOG_TAIL_CHARS = 4_000


@dataclass
class RunnerConfig:
    max_attempts: int = 3
    fail_fast: bool = False
    job_timeout_seconds: float = 300.0


class Runner:
    def __init__(
        self,
        repo_root: Path,
        driver: AgentDriver,
        config: RunnerConfig | None = None,
        executor: LocalExecutor | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.driver = driver
        self.config = config or RunnerConfig()
        self.executor = executor or LocalExecutor(
            repo_root, timeout_seconds=self.config.job_timeout_seconds,
        )

    async def run(
        self, jobs: list[CIJob], dry_run: bool = False,
    ) -> RunReport:
        results: list[JobResult] = []
        halted = False

        for idx, job in enumerate(jobs):
            if halted:
                if job.skip_reason:
                    results.append(
                        JobResult(
                            name=job.name,
                            status="skipped",
                            skip_reason=job.skip_reason,
                        )
                    )
                else:
                    results.append(
                        JobResult(name=job.name, status="not_run")
                    )
                continue

            if job.skip_reason:
                results.append(
                    JobResult(
                        name=job.name,
                        status="skipped",
                        skip_reason=job.skip_reason,
                    )
                )
                continue

            if dry_run:
                results.append(JobResult(name=job.name, status="not_run"))
                continue

            result = await self._run_single_job(job)
            results.append(result)

            if result.status == "escalated" and self.config.fail_fast:
                halted = True
                # mark remaining jobs
                for remaining in jobs[idx + 1 :]:
                    if remaining.skip_reason:
                        results.append(
                            JobResult(
                                name=remaining.name,
                                status="skipped",
                                skip_reason=remaining.skip_reason,
                            )
                        )
                    else:
                        results.append(
                            JobResult(name=remaining.name, status="not_run")
                        )
                break

        return RunReport(jobs=results, agent=self.driver.name)

    async def _run_single_job(self, job: CIJob) -> JobResult:
        last_log = ""

        for attempt in range(1, self.config.max_attempts + 1):
            exec_result = await self.executor.run_job(job)
            last_log = exec_result.log

            if exec_result.exit_code == 0:
                return JobResult(
                    name=job.name,
                    status="passed",
                    attempts=attempt,
                )

            if attempt >= self.config.max_attempts:
                return JobResult(
                    name=job.name,
                    status="escalated",
                    attempts=attempt,
                    driver=self.driver.name,
                    error_log=last_log[-_ERROR_LOG_TAIL_CHARS:],
                )

            context = FixContext(
                repo_root=self.repo_root,
                job_name=job.name,
                command=" && ".join(job.script),
                script=list(job.script),
                error_log=exec_result.log,
                attempt=attempt,
            )
            outcome = await self.driver.fix(context)

            if not outcome.applied:
                reason = outcome.reason or "driver did not apply a fix"
                return JobResult(
                    name=job.name,
                    status="escalated",
                    attempts=attempt,
                    driver=self.driver.name,
                    error_log=(
                        f"[driver: {reason}]\n\n"
                        + last_log[-_ERROR_LOG_TAIL_CHARS:]
                    ),
                )

        return JobResult(
            name=job.name,
            status="escalated",
            attempts=self.config.max_attempts,
            driver=self.driver.name,
            error_log=last_log[-_ERROR_LOG_TAIL_CHARS:],
        )
