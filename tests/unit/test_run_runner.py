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
    """With fail_fast + parallel, all jobs run in round 1. If both fail and
    exhaust attempts, both escalate."""
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
            ],
            "test": [
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
                ExecResult(log="x", exit_code=1),
            ],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor, max_attempts=3, fail_fast=True)
    report = await runner.run([_job("lint"), _job("test")])
    assert report.jobs[0].status == "escalated"
    assert report.jobs[1].status == "escalated"


@pytest.mark.asyncio
async def test_default_mode_continues_after_escalation(tmp_path: Path) -> None:
    """Both jobs run in parallel. lint fails all 3, test passes first try."""
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


# --- Parallel execution and batch fix tests ---


@pytest.mark.asyncio
async def test_parallel_all_pass_first_try(tmp_path: Path) -> None:
    """Multiple jobs all pass in parallel, no driver calls."""
    executor = _StubExecutor(
        results={
            "lint": [ExecResult(log="", exit_code=0)],
            "typecheck": [ExecResult(log="", exit_code=0)],
            "test": [ExecResult(log="", exit_code=0)],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint"), _job("typecheck"), _job("test")])
    assert all(j.status == "passed" for j in report.jobs)
    assert all(j.attempts == 1 for j in report.jobs)
    assert driver.calls == []


@pytest.mark.asyncio
async def test_batch_fix_resolves_all(tmp_path: Path) -> None:
    """Two jobs fail, batch fix resolves both on re-run."""
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="lint error", exit_code=1),
                ExecResult(log="", exit_code=0),
            ],
            "typecheck": [
                ExecResult(log="type error", exit_code=1),
                ExecResult(log="", exit_code=0),
            ],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint"), _job("typecheck")])

    assert report.jobs[0].status == "passed"
    assert report.jobs[0].attempts == 2
    assert report.jobs[1].status == "passed"
    assert report.jobs[1].attempts == 2
    # Only ONE driver call for the batch fix
    assert len(driver.calls) == 1
    # The batch context should mention both jobs
    assert "lint" in driver.calls[0].job_name
    assert "typecheck" in driver.calls[0].job_name


@pytest.mark.asyncio
async def test_batch_fix_partial_resolution(tmp_path: Path) -> None:
    """Two jobs fail. Batch fix resolves lint but typecheck still fails.
    Second batch fix resolves typecheck."""
    executor = _StubExecutor(
        results={
            "lint": [
                ExecResult(log="lint error", exit_code=1),
                ExecResult(log="", exit_code=0),  # passes after first fix
            ],
            "typecheck": [
                ExecResult(log="type error", exit_code=1),
                ExecResult(log="type error 2", exit_code=1),  # still fails
                ExecResult(log="", exit_code=0),  # passes after second fix
            ],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor, max_attempts=3)
    report = await runner.run([_job("lint"), _job("typecheck")])

    assert report.jobs[0].status == "passed"
    assert report.jobs[0].attempts == 2
    assert report.jobs[1].status == "passed"
    assert report.jobs[1].attempts == 3
    # Two driver calls: first batch (both jobs), second (only typecheck)
    assert len(driver.calls) == 2
    # Second call should be single-job (no batch), no prompt_override
    assert driver.calls[1].job_name == "typecheck"
    assert driver.calls[1].prompt_override is None


@pytest.mark.asyncio
async def test_driver_refusal_escalates_batch(tmp_path: Path) -> None:
    """Two jobs fail, driver refuses batch fix, both escalate."""
    executor = _StubExecutor(
        results={
            "lint": [ExecResult(log="fail", exit_code=1)],
            "typecheck": [ExecResult(log="fail", exit_code=1)],
        }
    )
    driver = _StubDriver(
        outcomes=[FixOutcome(applied=False, reason="cannot fix")]
    )
    runner = _runner(tmp_path, driver, executor, max_attempts=3)
    report = await runner.run([_job("lint"), _job("typecheck")])

    assert report.jobs[0].status == "escalated"
    assert report.jobs[1].status == "escalated"
    assert "cannot fix" in report.jobs[0].error_log
    assert "cannot fix" in report.jobs[1].error_log


@pytest.mark.asyncio
async def test_mixed_pass_fail_parallel(tmp_path: Path) -> None:
    """lint passes, typecheck fails. Only typecheck gets fixed."""
    executor = _StubExecutor(
        results={
            "lint": [ExecResult(log="", exit_code=0)],
            "typecheck": [
                ExecResult(log="type error", exit_code=1),
                ExecResult(log="", exit_code=0),
            ],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([_job("lint"), _job("typecheck")])

    assert report.jobs[0].status == "passed"
    assert report.jobs[0].attempts == 1
    assert report.jobs[1].status == "passed"
    assert report.jobs[1].attempts == 2
    assert len(driver.calls) == 1
    # Single failure, no batch prompt
    assert driver.calls[0].job_name == "typecheck"
    assert driver.calls[0].prompt_override is None


@pytest.mark.asyncio
async def test_watch_mode_parallel_no_fix(tmp_path: Path) -> None:
    """Watch mode (max_attempts=1): all jobs run in parallel, no fix attempted."""
    executor = _StubExecutor(
        results={
            "lint": [ExecResult(log="fail", exit_code=1)],
            "test": [ExecResult(log="", exit_code=0)],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor, max_attempts=1)
    report = await runner.run([_job("lint"), _job("test")])

    assert report.jobs[0].status == "escalated"
    assert report.jobs[1].status == "passed"
    assert driver.calls == []  # no fix in watch mode


@pytest.mark.asyncio
async def test_job_order_preserved(tmp_path: Path) -> None:
    """Report preserves original job order regardless of execution."""
    skipped = CIJob(
        name="deploy", stage="deploy", script=["true"],
        skip_reason="infra",
    )
    executor = _StubExecutor(
        results={
            "lint": [ExecResult(log="", exit_code=0)],
            "test": [ExecResult(log="", exit_code=0)],
        }
    )
    driver = _StubDriver()
    runner = _runner(tmp_path, driver, executor)
    report = await runner.run([skipped, _job("lint"), _job("test")])

    assert report.jobs[0].name == "deploy"
    assert report.jobs[0].status == "skipped"
    assert report.jobs[1].name == "lint"
    assert report.jobs[1].status == "passed"
    assert report.jobs[2].name == "test"
    assert report.jobs[2].status == "passed"
