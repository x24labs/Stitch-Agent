"""Tests for the `stitch run` CLI subcommand."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING, Any

import pytest

from runners.cli import build_parser, parse_cli_args
from runners.run_command import run_run_command

if TYPE_CHECKING:
    from pathlib import Path


def _make_args(**overrides: Any) -> argparse.Namespace:
    defaults = dict(
        agent="claude",
        repo=".",
        max_attempts=3,
        output="text",
        dry_run=False,
        fail_fast=False,
        jobs=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_parser_registers_run_subcommand() -> None:
    parser = build_parser()
    ns = parse_cli_args(parser, ["run", "claude", "--dry-run"])
    assert ns.command == "run"
    assert ns.agent == "claude"
    assert ns.dry_run is True


def test_parser_rejects_unknown_agent() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parse_cli_args(parser, ["run", "bogus"])


def test_parser_run_is_not_rewritten_to_fix() -> None:
    """Verify that 'run' is in _SUBCOMMANDS so it isn't shimmed to 'fix'."""
    parser = build_parser()
    ns = parse_cli_args(parser, ["run", "claude"])
    assert ns.command == "run"


@pytest.mark.asyncio
async def test_run_command_no_ci_config(tmp_path: Path) -> None:
    args = _make_args(repo=str(tmp_path), output="json")
    # capture stdout
    import io
    import sys

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        code = await run_run_command(args)
    finally:
        sys.stdout = old_stdout
    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["jobs"] == []


@pytest.mark.asyncio
async def test_run_command_dry_run(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text(
        "lint:\n  script: ruff check .\n"
        "deploy-prod:\n  script: kubectl apply -f k8s/\n"
    )
    args = _make_args(repo=str(tmp_path), dry_run=True)
    import io
    import sys

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        code = await run_run_command(args)
    finally:
        sys.stdout = old_stdout
    assert code == 0
    out = buf.getvalue()
    assert "lint" in out
    assert "deploy-prod" in out
    assert "skip" in out.lower()


@pytest.mark.asyncio
async def test_run_command_missing_repo() -> None:
    args = _make_args(repo="/nonexistent/path/definitely")
    code = await run_run_command(args)
    assert code == 2
