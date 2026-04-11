"""Data contracts for the stitch run package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pathlib import Path

JobStatus = Literal["passed", "escalated", "skipped", "not_run", "failed"]


@dataclass
class CIJob:
    """A CI job parsed from a platform-specific YAML file."""

    name: str
    stage: str
    script: list[str]
    image: str | None = None
    source_file: str = ""
    skip_reason: str | None = None


@dataclass
class FixContext:
    """Context passed to an AgentDriver when a job fails."""

    repo_root: Path
    job_name: str
    command: str
    script: list[str]
    error_log: str
    attempt: int


@dataclass
class FixOutcome:
    """Result of a driver fix attempt."""

    applied: bool
    reason: str = ""
    driver_log: str = ""


@dataclass
class JobResult:
    """Per-job result captured by the runner."""

    name: str
    status: JobStatus
    attempts: int = 0
    driver: str | None = None
    error_log: str = ""
    skip_reason: str | None = None


@dataclass
class RunReport:
    """Aggregate run report across all jobs."""

    jobs: list[JobResult] = field(default_factory=list)
    agent: str = ""

    @property
    def fixed_jobs(self) -> list[str]:
        """Job names that were fixed (passed after more than one attempt)."""
        return [
            j.name for j in self.jobs
            if j.status == "passed" and j.attempts > 1
        ]

    @property
    def overall_status(self) -> Literal["passed", "failed"]:
        non_skipped = [j for j in self.jobs if j.status != "skipped"]
        if not non_skipped:
            return "passed"
        return (
            "passed"
            if all(j.status == "passed" for j in non_skipped)
            else "failed"
        )

    def exit_code(self) -> int:
        return 0 if self.overall_status == "passed" else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "overall_status": self.overall_status,
            "jobs": [
                {
                    "name": j.name,
                    "status": j.status,
                    "attempts": j.attempts,
                    "driver": j.driver,
                    "skip_reason": j.skip_reason,
                    "error_log": j.error_log,
                }
                for j in self.jobs
            ],
        }
