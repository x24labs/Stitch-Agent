from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from stitch_agent.adapters.gitlab import GitLabAdapter
from stitch_agent.core.agent import StitchAgent
from stitch_agent.models import FixRequest
from stitch_agent.onboarding.connect import run_connect
from stitch_agent.onboarding.doctor import run_doctor_checks
from stitch_agent.onboarding.report import CommandReport
from stitch_agent.onboarding.setup import run_setup
from stitch_agent.settings import StitchSettings

_SUBCOMMANDS = {"fix", "ci", "setup", "doctor", "connect"}


def _add_fix_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--platform", choices=["gitlab", "github"], default="gitlab")
    parser.add_argument(
        "--project-id", required=True, help="GitLab: project ID or path. GitHub: owner/repo"
    )
    parser.add_argument("--pipeline-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--job-name", default=None)
    parser.add_argument("--gitlab-url", default=None, help="Override GitLab base URL")
    parser.add_argument("--github-url", default=None, help="Override GitHub API base URL")
    parser.add_argument(
        "--haiku-threshold",
        type=float,
        default=None,
        help="Confidence threshold for haiku types (default 0.80)",
    )
    parser.add_argument(
        "--sonnet-threshold",
        type=float,
        default=None,
        help="Confidence threshold for sonnet types (default 0.40)",
    )
    parser.add_argument("--output", choices=["json", "text"], default="text")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stitch",
        description="The AI that stitches your CI back together",
    )

    subparsers = p.add_subparsers(dest="command")

    fix_parser = subparsers.add_parser("fix", help="Analyze and fix a failed CI job")
    _add_fix_arguments(fix_parser)

    setup_parser = subparsers.add_parser("setup", help="Bootstrap stitch configuration")
    setup_parser.add_argument("--repo", default=".", help="Repository root path")
    setup_parser.add_argument("--platform", choices=["gitlab", "github"], default="gitlab")
    setup_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    connect_parser = subparsers.add_parser("connect", help="Connect stitch with CI provider")
    connect_parser.add_argument("--repo", default=".", help="Repository root path")
    connect_parser.add_argument("--platform", choices=["gitlab", "github"], default="gitlab")
    connect_parser.add_argument(
        "--project-id", default=None, help="GitHub repository in owner/repo format"
    )
    connect_parser.add_argument(
        "--webhook-url",
        default=None,
        help="Public stitch webhook endpoint, for example https://example.com/webhook/github",
    )
    connect_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    ci_parser = subparsers.add_parser(
        "ci", help="Run inside CI — auto-detect platform and fix failed jobs"
    )
    ci_parser.add_argument("--output", choices=["json", "text"], default="text")
    ci_parser.add_argument(
        "--platform",
        choices=["gitlab", "github"],
        default=None,
        help="Override auto-detection of CI platform",
    )
    ci_parser.add_argument(
        "--max-jobs",
        type=int,
        default=5,
        help="Max failed jobs to process (default 5)",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Run onboarding diagnostics")
    doctor_parser.add_argument("--repo", default=".", help="Repository root path")
    doctor_parser.add_argument("--platform", choices=["gitlab", "github"], default="gitlab")
    doctor_parser.add_argument(
        "--project-id", default=None, help="Provider project id for permission checks"
    )
    doctor_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    return p


def parse_cli_args(
    parser: argparse.ArgumentParser, argv: list[str] | None = None
) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] not in _SUBCOMMANDS and raw[0] not in {"-h", "--help"}:
        raw = ["fix", *raw]
    return parser.parse_args(raw)


async def run(args: argparse.Namespace) -> int:
    if args.command == "ci":
        return await run_ci_command(args)
    if args.command == "doctor":
        return await run_doctor(args)
    if args.command == "setup":
        return await run_setup_command(args)
    if args.command == "connect":
        return await run_connect_command(args)
    if args.command not in {None, "fix"}:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1

    return await run_fix(args)


async def run_fix(args: argparse.Namespace) -> int:
    settings = StitchSettings()
    request = FixRequest(
        platform=args.platform,
        project_id=args.project_id,
        pipeline_id=args.pipeline_id,
        job_id=args.job_id,
        branch=args.branch,
        job_name=args.job_name,
    )

    if args.platform == "gitlab":
        base_url = args.gitlab_url or settings.gitlab_base_url
        adapter = GitLabAdapter(token=settings.gitlab_token, base_url=base_url)
    elif args.platform == "github":
        from stitch_agent.adapters.github import GitHubAdapter

        base_url = args.github_url or settings.github_base_url
        adapter = GitHubAdapter(token=settings.github_token, base_url=base_url)
    else:
        print(f"Unknown platform: {args.platform}", file=sys.stderr)
        return 1

    haiku_thresh = (
        args.haiku_threshold
        if args.haiku_threshold is not None
        else settings.haiku_confidence_threshold
    )
    sonnet_thresh = (
        args.sonnet_threshold
        if args.sonnet_threshold is not None
        else settings.sonnet_confidence_threshold
    )

    agent = StitchAgent(
        adapter=adapter,
        anthropic_api_key=settings.anthropic_api_key,
        haiku_confidence_threshold=haiku_thresh,
        sonnet_confidence_threshold=sonnet_thresh,
        max_attempts=settings.max_attempts,
    )

    try:
        async with adapter:
            result = await agent.fix(request)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.output == "json":
        print(json.dumps(result.model_dump(), indent=2))
    else:
        icons = {"fixed": "\u2705", "escalate": "\u26a0\ufe0f", "error": "\u274c"}
        icon = icons.get(result.status, "?")
        print(f"{icon} Status:     {result.status}")
        print(f"   Type:       {result.error_type.value}")
        print(f"   Confidence: {result.confidence:.0%}")
        print(f"   Reason:     {result.reason}")
        if result.mr_url:
            print(f"   MR URL:     {result.mr_url}")
        if result.escalation_reason_code:
            print(f"   Code:       {result.escalation_reason_code}")

    return 0 if result.status == "fixed" else 1


async def run_ci_command(args: argparse.Namespace) -> int:
    from runners.ci_runner import run_ci

    return await run_ci(
        output_format=args.output,
        platform_override=args.platform,
        max_jobs=args.max_jobs,
    )


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


def _run_not_implemented(command: str, *, json_output: bool) -> int:
    report = CommandReport(
        command=command,
        ok=False,
        prompts=[f"{command} command is not implemented yet"],
        warnings=[f"{command} is in progress"],
        next_steps=["Run `stitch doctor --json` to verify current readiness"],
    )
    if json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"{command} is not implemented yet")
        print("Run `stitch doctor --json` to verify current readiness")
    return report.exit_code()


async def run_setup_command(args: argparse.Namespace) -> int:
    report = run_setup(repo_root=Path(args.repo), platform=args.platform)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_doctor_report(report)
    return report.exit_code()


async def run_connect_command(args: argparse.Namespace) -> int:
    settings = StitchSettings()
    report = await run_connect(
        platform=args.platform,
        repo_root=Path(args.repo),
        project_id=args.project_id,
        webhook_url=args.webhook_url,
        settings=settings,
    )
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
