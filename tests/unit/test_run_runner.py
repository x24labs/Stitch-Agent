"""Tests for stitch_agent.run.runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from stitch_agent.run.executor import ExecResult, LocalExecutor
from stitch_agent.run.models import CIJob, FixContext, FixOutcome, JobResult, RunReport
from stitch_agent.run.runner import Runner, RunnerConfig

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class _StubExecutor:
    """Returns queued ExecResults per job name. Counts calls."""

    results: dict[str, list[ExecResult]] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)

    async def run_job(self, job: CIJob) -> ExecResult:
        self.calls[job.name] = self.calls.get(job.name, 0) + 1
        queue = self.results.get(job.name, [])
        if not queue:
            return ExecResult(log="", exit_code=0)
        return queue.pop(0)


@dataclass
class _StubDriver:
    name: str = "stub"
    outcomes: list[FixOutcome] = field(default_factory=list)
    calls: list[FixContext] = field(default_factory=list)

    async def fix(self, context: FixContext) -> FixOutcome:
        self.calls.append(context)
        if self.outcomes:
            return self.outcomes.pop(0)
        return FixOutcome(applied=True, reason="stub fix applied")


def _job(name: str) -> CIJob:
    return CIJob(name=name, stage="test", script=["echo hi"])


def _runner(
    tmp_path: Path,
    driver: _StubDriver,
    executor: _StubExecutor,
    *,
    max_attempts: int = 3,
    fail_fast: bool = False,
) -> Runner:
    return Runner(
        repo_root=tmp_path,
        driver=driver,  # type: ignore[arg-type]
        config=RunnerConfig(max_attempts=max_attempts, fail_fast=fail_fast),
        executor=executor,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_job_passes_first_try(tmp_path: Path) -> None:
    executor = _StubExecutor(results={"lint": [ExecResult(log="", exit_code=0)]})
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint")])
    assert report.jobs[0].status == "passed"
    assert report.jobs[0].attempts == 1
    assert driver.calls == []


@pytest.mark.asyncio
async def test_job_passes_after_one_fix(tmp_path: Path) -> None:
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="fail", exit_code=1),
                ExecResult(log="", exit_code=0),
            ]
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint")])
    assert report.jobs[0].status == "passed"
    assert report.jobs[0].attempts == 2
    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_job_exhausts_attempts(tmp_path: Path) -> None:
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="fail1", exit_code=1),
                ExecResult(log="fail2", exit_code=1),
                ExecResult(log="fail3", exit_code=1),
            ]
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor, max_attempts=3)
    report = await runner.run([_job("lint")])
    assert report.jobs[0].status == "escalated"
    assert report.jobs[0].attempts == 3
    assert len(driver.calls) == 2  # fix called between attempts


@pytest.mark.asyncio
async def test_driver_refusal_escalates_immediately(tmp_path: Path) -> None:
    executor = _StubExecutor(
        results={"lint": [ExecResult(log="fail", exit_code=1)]}
    )
    driver = _StubDriver(
        outcomes=[FixOutcome(applied=False, reason="no can do")]
    )
    runner = _runner(tmp_path, driver, executor, max_attempts=3)
    report = await runner.run([_job("lint")])
    assert report.jobs[0].status == "escalated"
    assert "no can do" in report.jobs[0].error_log


@pytest.mark.asyncio
async def test_fail_fast_marks_remaining_not_run(tmp_path: Path) -> None:
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
            ],
            "test": [ExecResult(log="", exit_code=0)],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor, max_attempts=3, fail_fast=True)
    report = await runner.run([_job("lint"), _job("test")])
    assert report.jobs[0].status == "escalated"
    assert report.jobs[1].status == "not_run"


@pytest.mark.asyncio
async def test_default_mode_continues_after_escalation(tmp_path: Path) -> None:
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
            ],
            "test": [ExecResult(log="", exit_code=0)],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor, max_attempts=3, fail_fast=False)
    report = await runner.run([_job("lint"), _job("test")])
    assert report.jobs[0].status == "escalated"
    assert report.jobs[1].status == "passed"


@pytest.mark.asyncio
async def test_dry_run_does_not_execute(tmp_path: Path) -> None:
    executor = _StubExecutor()
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint")], dry_run=True)
    assert report.jobs[0].status == "not_run"
    assert executor.calls == {}


@pytest.mark.asyncio
async def test_skipped_job_reported_without_execution(tmp_path: Path) -> None:
    executor = _StubExecutor()
    driver = _StubDriver()
    job = CIJob(
        name="deploy",
        stage="deploy",
        script=["true"],
        skip_reason="matches skip pattern",
    )
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([job])
    assert report.jobs[0].status == "skipped"
    assert executor.calls == {}


@pytest.mark.asyncio
async def test_run_report_exit_code_on_success(tmp_path: Path) -> None:
    executor = _StubExecutor(results={"lint": [ExecResult(log="", exit_code=0)]})
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint")])
    assert report.exit_code() == 0
    assert report.overall_status == "passed"


@pytest.mark.asyncio
async def test_run_report_exit_code_on_failure(tmp_path: Path) -> None:
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
            ]
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint")])
    assert report.exit_code() == 1
    assert report.overall_status == "failed"


def test_fixed_jobs_mixed_results() -> None:
    report = RunReport(jobs=[
        JobResult(name="lint", status="passed", attempts=2),
        JobResult(name="typecheck", status="passed", attempts=1),
        JobResult(name="test", status="passed", attempts=1),
    ])
    assert report.fixed_jobs == ["lint"]


def test_fixed_jobs_none_fixed() -> None:
    report = RunReport(jobs=[
        JobResult(name="lint", status="passed", attempts=1),
        JobResult(name="test", status="passed", attempts=1),
    ])
    assert report.fixed_jobs == []


def test_fixed_jobs_all_passed_first_try() -> None:
    report = RunReport(jobs=[
        JobResult(name="lint", status="passed", attempts=1),
    ])
    assert report.fixed_jobs == []


def test_fixed_jobs_excludes_escalated() -> None:
    report = RunReport(jobs=[
        JobResult(name="lint", status="escalated", attempts=3),
        JobResult(name="test", status="passed", attempts=2),
    ])
    assert report.fixed_jobs == ["test"]


@pytest.mark.asyncio
async def test_real_executor_integration(tmp_path: Path) -> None:
    """Smoke test with a real LocalExecutor against a passing shell command."""
    driver = _StubDriver()
    runner = Runner(
        repo_root=tmp_path,
        driver=driver,  # type: ignore[arg-type]
        config=RunnerConfig(max_attempts=1),
        executor=LocalExecutor(tmp_path),
    )
    report = await runner.run([_job("hello")])
    assert report.jobs[0].status == "passed"
