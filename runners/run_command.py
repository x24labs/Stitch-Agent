"""CLI handler for `stitch run <agent>`."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from stitch_agent.run.ci_parser import CIParseError, parse_ci_config
from stitch_agent.run.drivers import (
    AgentDriver,
    ApiDriver,
    ClaudeCodeDriver,
    CodexDriver,
)
from stitch_agent.run.filter import apply_filter, load_filter_config
from stitch_agent.run.runner import Runner, RunnerConfig
from stitch_agent.run.watcher import (
    LockAcquireError,
    StitchLock,
    WatchConfig,
    wait_for_change_then_idle,
)
from stitch_agent.settings import StitchSettings

if TYPE_CHECKING:
    import argparse

    from stitch_agent.run.models import CIJob, JobResult, RunReport

_VALID_AGENTS = ("claude", "codex", "api")

_STATUS_ICONS = {
    "passed": "\u2705",
    "escalated": "\u274c",
    "skipped": "\u23ed\ufe0f",
    "not_run": "\u2796",
    "failed": "\u274c",
}


def _build_driver(agent: str) -> AgentDriver | None:
    if agent == "claude":
        return ClaudeCodeDriver()
    if agent == "codex":
        return CodexDriver()
    if agent == "api":
        settings = StitchSettings()
        return ApiDriver(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    return None


def _print_dry_run(jobs: list[CIJob]) -> None:
    runnable = [j for j in jobs if not j.skip_reason]
    skipped = [j for j in jobs if j.skip_reason]
    print(f"stitch run: dry-run — {len(runnable)} runnable, {len(skipped)} skipped")
    for j in runnable:
        cmd = " && ".join(j.script)[:100]
        print(f"  \u2022 [{j.stage}] {j.name}: {cmd}")
    for j in skipped:
        print(f"  \u23ed\ufe0f  [{j.stage}] {j.name} — {j.skip_reason}")


def _print_report(report: RunReport) -> None:
    print(f"stitch run [{report.agent}]: {report.overall_status}")
    for j in report.jobs:
        _print_job_result(j)


def _print_job_result(j: JobResult) -> None:
    icon = _STATUS_ICONS.get(j.status, "?")
    tail = ""
    if j.attempts:
        tail = f" ({j.attempts} attempt{'s' if j.attempts > 1 else ''})"
    print(f"  {icon} {j.name}{tail}")
    if j.status == "skipped" and j.skip_reason:
        print(f"       reason: {j.skip_reason}")
    if j.status == "escalated" and j.error_log:
        snippet = j.error_log.strip().splitlines()[-3:]
        for line in snippet:
            print(f"       {line[:140]}")


async def run_run_command(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"Error: repo path not found: {repo_root}", file=sys.stderr)
        return 2

    try:
        all_jobs = parse_ci_config(repo_root)
    except CIParseError as exc:
        print(f"Error parsing CI config: {exc}", file=sys.stderr)
        return 2

    if not all_jobs:
        msg = "No CI configuration found (.gitlab-ci.yml or .github/workflows/)"
        if args.output == "json":
            print(json.dumps({"agent": args.agent, "jobs": [], "reason": msg}))
        else:
            print(msg)
        return 0

    filter_cfg = load_filter_config(repo_root)
    if args.jobs:
        filter_cfg.only = [j.strip() for j in args.jobs.split(",") if j.strip()]

    jobs = apply_filter(all_jobs, filter_cfg)

    if args.agent not in _VALID_AGENTS:
        print(
            f"Unknown agent: {args.agent}. Valid: {', '.join(_VALID_AGENTS)}",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        _print_dry_run(jobs)
        return 0

    driver = _build_driver(args.agent)
    if driver is None:
        print(f"Unknown agent: {args.agent}", file=sys.stderr)
        return 2

    if getattr(args, "watch", False):
        return await _run_watch_mode(repo_root, driver, jobs, args)

    config = RunnerConfig(
        max_attempts=args.max_attempts,
        fail_fast=args.fail_fast,
    )
    runner = Runner(repo_root=repo_root, driver=driver, config=config)
    report = await runner.run(jobs, dry_run=False)

    if args.output == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_report(report)

    return report.exit_code()


def _timestamp() -> str:
    return time.strftime("%H:%M:%S")


def _runnable_names(jobs: list[CIJob]) -> list[str]:
    return [j.name for j in jobs if not j.skip_reason]


async def _run_watch_mode(
    repo_root: Path,
    driver: AgentDriver,
    jobs: list[CIJob],
    args: argparse.Namespace,
) -> int:
    """Watch mode: run jobs once, then re-run on each debounced filesystem change.

    Phase A behavior: no-fix. `max_attempts` is forced to 1 so the driver is
    never invoked. Jobs that fail are reported but no agent is spawned. This
    keeps the user in full control of their editor/AI workflow.
    """
    runnable = _runnable_names(jobs)
    if not runnable:
        print("stitch watch: nothing to run — all jobs are skipped", file=sys.stderr)
        return 0

    config = RunnerConfig(
        max_attempts=1,  # Phase A: no-fix
        fail_fast=False,
    )
    runner = Runner(repo_root=repo_root, driver=driver, config=config)
    watch_cfg = WatchConfig(debounce_seconds=args.debounce)

    lock = StitchLock(repo_root)
    try:
        lock.acquire()
    except LockAcquireError as exc:
        print(f"stitch watch: {exc}", file=sys.stderr)
        return 2

    try:
        print(f"\nstitch watch [{driver.name}]: monitoring {repo_root}")
        print(f"  debounce: {args.debounce:.1f}s")
        print(f"  jobs:     {', '.join(runnable)}")
        print("  mode:     no-fix (reports only, never invokes agent)")
        print("  press Ctrl+C to stop\n")

        # Initial run so the user has an immediate baseline
        print(f"[{_timestamp()}] initial run")
        report = await runner.run(jobs, dry_run=False)
        _print_report(report)

        # Watch loop
        while True:
            try:
                await wait_for_change_then_idle(repo_root, watch_cfg)
            except asyncio.CancelledError:
                break
            print(f"\n[{_timestamp()}] changes settled, re-running jobs")
            report = await runner.run(jobs, dry_run=False)
            _print_report(report)
    except KeyboardInterrupt:
        print("\nstitch watch: stopped")
    finally:
        lock.release()

    return 0
