"""stitch CLI -- skill-first local CI runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from stitch_agent.onboarding.doctor import run_doctor_checks
from stitch_agent.onboarding.setup import run_setup
from stitch_agent.settings import StitchSettings

if TYPE_CHECKING:
    from stitch_agent.onboarding.report import CommandReport

_SUBCOMMANDS = {"run", "setup", "doctor"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stitch",
        description="Run your CI jobs locally. Fix failures with AI.",
    )

    subparsers = p.add_subparsers(dest="command")

    # --- run ---
    run_parser = subparsers.add_parser(
        "run",
        help="Run CI jobs locally with an AI fix loop",
    )
    run_parser.add_argument(
        "agent",
        choices=["claude", "codex"],
        help="Which agent to delegate fixes to",
    )
    run_parser.add_argument("--repo", default=".", help="Repository root path")
    run_parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum fix attempts per job (default 3)",
    )
    run_parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List runnable jobs without executing them",
    )
    run_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first escalated job",
    )
    run_parser.add_argument(
        "--jobs",
        default=None,
        help="Comma-separated allowlist of job names to run (prefix match)",
    )
    run_parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Watch mode: run jobs once, then re-run whenever files settle "
            "after changes. Reports only, never invokes the agent. "
            "Press Ctrl+C to stop."
        ),
    )
    run_parser.add_argument(
        "--debounce",
        type=float,
        default=3.0,
        help="Seconds of filesystem quiet before re-running in watch mode",
    )

    # --- setup ---
    setup_parser = subparsers.add_parser("setup", help="Bootstrap stitch configuration")
    setup_parser.add_argument("--repo", default=".", help="Repository root path")
    setup_parser.add_argument("--platform", choices=["gitlab", "github"], default="gitlab")
    setup_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # --- doctor ---
    doctor_parser = subparsers.add_parser("doctor", help="Check your environment")
    doctor_parser.add_argument("--repo", default=".", help="Repository root path")
    doctor_parser.add_argument("--platform", choices=["gitlab", "github"], default="gitlab")
    doctor_parser.add_argument(
        "--project-id", default=None, help="Provider project id for permission checks"
    )
    doctor_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    return p


def parse_cli_args(
    parser: argparse.ArgumentParser, argv: list[str] | None = None,
) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    return parser.parse_args(raw)


async def run(args: argparse.Namespace) -> int:
    if args.command == "run":
        from runners.run_command import run_run_command

        return await run_run_command(args)
    if args.command == "doctor":
        return await run_doctor(args)
    if args.command == "setup":
        return await run_setup_command(args)
    print("Use 'stitch run <agent>' to get started.", file=sys.stderr)
    return 1


async def run_doctor(args: argparse.Namespace) -> int:
    settings = StitchSettings()
    report = await run_doctor_checks(
        platform=args.platform,
        repo_root=Path(args.repo),
        settings=settings,
        project_id=args.project_id,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_doctor_report(report)
    return report.exit_code()


async def run_setup_command(args: argparse.Namespace) -> int:
    report = run_setup(repo_root=Path(args.repo), platform=args.platform)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_doctor_report(report)
    return report.exit_code()


def _print_doctor_report(report: CommandReport) -> None:
    status = "ok" if report.ok else "failed"
    print(f"stitch doctor: {status}")
    for check in report.checks:
        print(f"- [{check.status}] {check.id}: {check.message}")
        if check.remediation:
            print(f"  remediation: {check.remediation}")
    if report.next_steps:
        print("next steps:")
        for step in report.next_steps:
            print(f"- {step}")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parse_cli_args(parser, argv=argv)
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
