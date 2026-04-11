"""stitch CLI -- skill-first local CI runner."""

from __future__ import annotations

import argparse
import asyncio
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stitch",
        description="Run your CI jobs locally. Fix failures with AI.",
    )

    subparsers = p.add_subparsers(dest="command")

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
        "--no-push",
        action="store_true",
        help="Commit fixes locally but skip pushing to remote",
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
    print("Usage: stitch run <claude|codex> [options]", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parse_cli_args(parser, argv=argv)
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
