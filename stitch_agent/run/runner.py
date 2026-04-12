"""Runner loop -- orchestrate local CI execution with an AI fix loop.

Jobs are executed in parallel via asyncio.gather. When multiple jobs fail,
their errors are batched into a single driver prompt to minimize LLM calls.
After each batch fix, all previously-failed jobs are re-run in parallel to
check which ones the fix resolved.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from stitch_agent.run.drivers.base import build_batch_prompt
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
    from stitch_agent.run.executor import ExecResult

_ERROR_LOG_TAIL_CHARS = 4_000


class RunnerCallback(Protocol):
    """Callback protocol for UI integration."""

    def job_started(self, name: str, attempt: int, max_attempts: int) -> None: ...
    def job_log_update(self, name: str, log: str) -> None: ...
    def job_finished(self, name: str, result: JobResult) -> None: ...
    def driver_started(self, name: str, driver_name: str) -> None: ...
    def driver_log_update(self, name: str, log: str) -> None: ...


class _NullCallback:
    def job_started(self, name: str, attempt: int, max_attempts: int) -> None:
        pass

    def job_log_update(self, name: str, log: str) -> None:
        pass

    def job_finished(self, name: str, result: JobResult) -> None:
        pass

    def driver_started(self, name: str, driver_name: str) -> None:
        pass

    def driver_log_update(self, name: str, log: str) -> None:
        pass


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
        callback: RunnerCallback | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.driver = driver
        self.config = config or RunnerConfig()
        self.executor = executor or LocalExecutor(
            repo_root, timeout_seconds=self.config.job_timeout_seconds,
        )
        self._cb: RunnerCallback = callback or _NullCallback()

    async def run(
        self, jobs: list[CIJob], dry_run: bool = False,
    ) -> RunReport:
        results: dict[str, JobResult] = {}
        runnable: list[CIJob] = []

        for job in jobs:
            if job.skip_reason:
                results[job.name] = JobResult(
                    name=job.name,
                    status="skipped",
                    skip_reason=job.skip_reason,
                )
            elif dry_run:
                results[job.name] = JobResult(name=job.name, status="not_run")
            else:
                runnable.append(job)

        if not runnable:
            return RunReport(
                jobs=[results[j.name] for j in jobs],
                agent=self.driver.name,
            )

        pending = list(runnable)

        for attempt in range(1, self.config.max_attempts + 1):
            # Run all pending jobs in parallel
            exec_results = await self._run_jobs_parallel(pending, attempt)

            # Partition into passed and failed
            failed: list[tuple[CIJob, str]] = []
            for job in pending:
                er = exec_results[job.name]
                if er.exit_code == 0:
                    result = JobResult(
                        name=job.name, status="passed", attempts=attempt,
                    )
                    results[job.name] = result
                    self._cb.job_finished(job.name, result)
                else:
                    failed.append((job, er.log))

            if not failed:
                break

            # Last attempt: escalate all remaining failures
            if attempt >= self.config.max_attempts:
                for job, log in failed:
                    result = JobResult(
                        name=job.name,
                        status="escalated",
                        attempts=attempt,
                        driver=self.driver.name,
                        error_log=log[-_ERROR_LOG_TAIL_CHARS:],
                    )
                    results[job.name] = result
                    self._cb.job_finished(job.name, result)
                break

            # Batch fix all failures in a single driver call
            contexts = [
                FixContext(
                    repo_root=self.repo_root,
                    job_name=job.name,
                    command=" && ".join(job.script),
                    script=list(job.script),
                    error_log=log,
                    attempt=attempt,
                )
                for job, log in failed
            ]

            batch_context = self._make_batch_context(contexts)
            batch_label = ", ".join(j.name for j, _ in failed)

            self._cb.driver_started(batch_label, self.driver.name)

            self.driver.on_output = lambda log, _label=batch_label: (
                self._cb.driver_log_update(_label, log)
            )

            outcome = await self.driver.fix(batch_context)
            self.driver.on_output = None

            if not outcome.applied:
                reason = outcome.reason or "driver did not apply a fix"
                for job, log in failed:
                    result = JobResult(
                        name=job.name,
                        status="escalated",
                        attempts=attempt,
                        driver=self.driver.name,
                        error_log=(
                            f"[driver: {reason}]\n\n"
                            + log[-_ERROR_LOG_TAIL_CHARS:]
                        ),
                    )
                    results[job.name] = result
                    self._cb.job_finished(job.name, result)
                break

            # Next round: re-run only the jobs that failed
            pending = [job for job, _ in failed]

            if self.config.fail_fast:
                # In parallel model, fail_fast halts after escalation
                # (max_attempts or driver refusal), both handled above.
                pass

        # Build report preserving original job order
        ordered = [
            results.get(j.name, JobResult(name=j.name, status="not_run"))
            for j in jobs
        ]
        return RunReport(jobs=ordered, agent=self.driver.name)

    async def _run_jobs_parallel(
        self, jobs: list[CIJob], attempt: int,
    ) -> dict[str, ExecResult]:
        """Run multiple jobs concurrently, return results keyed by name."""
        for job in jobs:
            self._cb.job_started(job.name, attempt, self.config.max_attempts)

        async def run_one(job: CIJob) -> tuple[str, ExecResult]:
            result = await self.executor.run_job(job)
            self._cb.job_log_update(job.name, result.log)
            return job.name, result

        pairs = await asyncio.gather(*(run_one(j) for j in jobs))
        return dict(pairs)

    def _make_batch_context(
        self, contexts: list[FixContext],
    ) -> FixContext:
        """Merge multiple FixContexts into one with a batch prompt."""
        if len(contexts) == 1:
            return contexts[0]

        prompt = build_batch_prompt(contexts)
        return FixContext(
            repo_root=contexts[0].repo_root,
            job_name=", ".join(c.job_name for c in contexts),
            command="(batch fix)",
            script=[],
            error_log="",
            attempt=contexts[0].attempt,
            prompt_override=prompt,
        )
