"""Tests for runners/cli.py (v1.0 -- run subcommand only)."""

from __future__ import annotations

import pytest

from runners.cli import build_parser, parse_cli_args


def test_parse_run_subcommand() -> None:
    parser = build_parser()
    args = parse_cli_args(parser, ["run", "claude", "--dry-run"])
    assert args.command == "run"
    assert args.agent == "claude"
    assert args.dry_run is True


def test_parse_run_codex() -> None:
    parser = build_parser()
    args = parse_cli_args(parser, ["run", "codex"])
    assert args.command == "run"
    assert args.agent == "codex"


def test_unknown_agent_rejected() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parse_cli_args(parser, ["run", "bogus"])


def test_no_subcommand() -> None:
    parser = build_parser()
    args = parse_cli_args(parser, [])
    assert args.command is None
