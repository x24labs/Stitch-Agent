"""CLI handler for `stitch run <agent>`."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from stitch_agent.run.ci_parser import CIParseError, parse_ci_config
from stitch_agent.run.drivers import (
    AgentDriver,
    ClaudeCodeDriver,
    CodexDriver,
)
from stitch_agent.run.filter import apply_filter, load_filter_config
from stitch_agent.run.runner import Runner, RunnerConfig
from stitch_agent.run.ui import RunUI, print_summary
from stitch_agent.run.watcher import (
    LockAcquireError,
    StitchLock,
    WatchConfig,
    wait_for_change_then_idle,
)

if TYPE_CHECKING:
    import argparse

    from stitch_agent.run.models import CIJob

_VALID_AGENTS = ("claude", "codex")


def _build_driver(agent: str) -> AgentDriver | None:
    if agent == "claude":
        return ClaudeCodeDriver()
    if agent == "codex":
        return CodexDriver()
    return None


def _print_dry_run(jobs: list[CIJob]) -> None:
    console = Console()
    runnable = [j for j in jobs if not j.skip_reason]
    skipped = [j for j in jobs if j.skip_reason]
    console.print(
        f"[bold]stitch run[/]: dry-run "
        f"[cyan]{len(runnable)} runnable[/], [dim]{len(skipped)} skipped[/]"
    )
    for j in runnable:
        cmd = " && ".join(j.script)[:80]
        console.print(f"  [green]\u25b6[/] [dim]\\[{j.stage}][/] [bold]{j.name}[/]: {cmd}")
    for j in skipped:
        console.print(f"  [dim]\u23ed\ufe0f  \\[{j.stage}] {j.name} -- {j.skip_reason}[/]")


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

    # Normal run with TUI
    config = RunnerConfig(
        max_attempts=args.max_attempts,
        fail_fast=args.fail_fast,
    )

    if args.output == "json":
        runner = Runner(repo_root=repo_root, driver=driver, config=config)
        report = await runner.run(jobs, dry_run=False)
        print(json.dumps(report.to_dict(), indent=2))
        return report.exit_code()

    # Rich TUI mode
    console = Console()
    ui = RunUI(console=console, agent=args.agent, repo=str(repo_root))
    ui.init_jobs(jobs)
    runner = Runner(repo_root=repo_root, driver=driver, config=config, callback=ui)

    ui.start()
    try:
        report = await runner.run(jobs, dry_run=False)
    finally:
        ui.stop()

    print_summary(console, report)
    return report.exit_code()


def _runnable_names(jobs: list[CIJob]) -> list[str]:
    return [j.name for j in jobs if not j.skip_reason]


async def _run_watch_mode(
    repo_root: Path,
    driver: AgentDriver,
    jobs: list[CIJob],
    args: argparse.Namespace,
) -> int:
    """Watch mode with TUI. No-fix (max_attempts=1)."""
    runnable = _runnable_names(jobs)
    if not runnable:
        print("stitch watch: nothing to run -- all jobs are skipped", file=sys.stderr)
        return 0

    config = RunnerConfig(max_attempts=1, fail_fast=False)
    console = Console()
    ui = RunUI(console=console, agent=args.agent, repo=str(repo_root))
    ui.init_jobs(jobs)
    runner = Runner(repo_root=repo_root, driver=driver, config=config, callback=ui)
    watch_cfg = WatchConfig(debounce_seconds=args.debounce)

    lock = StitchLock(repo_root)
    try:
        lock.acquire()
    except LockAcquireError as exc:
        print(f"stitch watch: {exc}", file=sys.stderr)
        return 2

    cycle = 0
    try:
        console.print(
            f"\n[bold]stitch watch[/] [cyan]\\[{args.agent}][/]: "
            f"monitoring [bold]{repo_root}[/]"
        )
        console.print(f"  debounce: {args.debounce:.1f}s, jobs: {', '.join(runnable)}")
        console.print("  mode: [yellow]no-fix[/] (reports only)")
        console.print("  press [bold]Ctrl+C[/] to stop\n")

        # Initial run
        ui.start()
        report = await runner.run(jobs, dry_run=False)
        ui.stop()
        print_summary(console, report)

        # Watch loop
        while True:
            try:
                await wait_for_change_then_idle(repo_root, watch_cfg)
            except asyncio.CancelledError:
                break
            cycle += 1
            console.print(f"\n[dim][{time.strftime('%H:%M:%S')}] changes settled, re-running[/]")
            ui.init_jobs(jobs)
            ui.watch_cycle(cycle)
            ui.start()
            report = await runner.run(jobs, dry_run=False)
            ui.stop()
            print_summary(console, report)
    except KeyboardInterrupt:
        ui.stop()
        console.print("\n[dim]stitch watch: stopped[/]")
    finally:
        lock.release()

    return 0
