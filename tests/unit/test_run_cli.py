"""Tests for the `stitch run` CLI subcommand."""

from __future__ import annotations

import argparse
import io
import json
from typing import TYPE_CHECKING, Any

import pytest

from runners.cli import build_parser, parse_cli_args
from runners.run_command import _auto_commit_push, run_run_command
from stitch_agent.run.git import CommitResult, GitSnapshot, PushResult
from stitch_agent.run.models import JobResult, RunReport

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


def test_parser_accepts_no_push_flag() -> None:
    parser = build_parser()
    ns = parse_cli_args(parser, ["run", "claude", "--no-push"])
    assert ns.no_push is True


def test_parser_no_push_default_false() -> None:
    parser = build_parser()
    ns = parse_cli_args(parser, ["run", "claude"])
    assert ns.no_push is False


# --- _auto_commit_push decision logic ---


def _snap_pushable() -> GitSnapshot:
    return GitSnapshot(clean=True, branch="main", has_remote=True, ahead=0)


def _snap_dirty() -> GitSnapshot:
    return GitSnapshot(clean=False, branch="main", has_remote=True, ahead=0)


def _report_fixed() -> RunReport:
    return RunReport(
        jobs=[JobResult(name="lint", status="passed", attempts=2)],
        agent="claude",
    )


def _report_no_fix() -> RunReport:
    return RunReport(
        jobs=[JobResult(name="lint", status="passed", attempts=1)],
        agent="claude",
    )


def _report_failed() -> RunReport:
    return RunReport(
        jobs=[JobResult(name="lint", status="escalated", attempts=3)],
        agent="claude",
    )


def test_auto_commit_push_all_conditions_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {"commit": 0, "push": 0}

    def mock_commit(repo_root: Any, fixed_jobs: Any) -> CommitResult:
        calls["commit"] += 1
        return CommitResult(ok=True, sha="a" * 40, message="fix(stitch): lint")

    def mock_push(repo_root: Any) -> PushResult:
        calls["push"] += 1
        return PushResult(ok=True)

    monkeypatch.setattr("runners.run_command.commit", mock_commit)
    monkeypatch.setattr("runners.run_command.push", mock_push)

    from rich.console import Console
    console = Console(file=io.StringIO())
    _auto_commit_push(console, tmp_path, _snap_pushable(), _report_fixed(), no_push=False)
    assert calls["commit"] == 1
    assert calls["push"] == 1


def test_auto_commit_push_dirty_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {"commit": 0, "push": 0}

    def mock_commit(repo_root: Any, fixed_jobs: Any) -> CommitResult:
        calls["commit"] += 1
        return CommitResult(ok=True, sha="a" * 40, message="")

    def mock_push(repo_root: Any) -> PushResult:
        calls["push"] += 1
        return PushResult(ok=True)

    monkeypatch.setattr("runners.run_command.commit", mock_commit)
    monkeypatch.setattr("runners.run_command.push", mock_push)

    from rich.console import Console
    console = Console(file=io.StringIO())
    _auto_commit_push(console, tmp_path, _snap_dirty(), _report_fixed(), no_push=False)
    assert calls["commit"] == 0
    assert calls["push"] == 0


def test_auto_commit_push_no_fix_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {"commit": 0}

    def mock_commit(repo_root: Any, fixed_jobs: Any) -> CommitResult:
        calls["commit"] += 1
        return CommitResult(ok=True, sha="a" * 40, message="")

    monkeypatch.setattr("runners.run_command.commit", mock_commit)

    from rich.console import Console
    console = Console(file=io.StringIO())
    _auto_commit_push(console, tmp_path, _snap_pushable(), _report_no_fix(), no_push=False)
    assert calls["commit"] == 0


def test_auto_commit_push_failed_report_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {"commit": 0}

    def mock_commit(repo_root: Any, fixed_jobs: Any) -> CommitResult:
        calls["commit"] += 1
        return CommitResult(ok=True, sha="a" * 40, message="")

    monkeypatch.setattr("runners.run_command.commit", mock_commit)

    from rich.console import Console
    console = Console(file=io.StringIO())
    _auto_commit_push(console, tmp_path, _snap_pushable(), _report_failed(), no_push=False)
    assert calls["commit"] == 0


def test_auto_commit_push_no_push_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {"commit": 0, "push": 0}

    def mock_commit(repo_root: Any, fixed_jobs: Any) -> CommitResult:
        calls["commit"] += 1
        return CommitResult(ok=True, sha="a" * 40, message="fix(stitch): lint")

    def mock_push(repo_root: Any) -> PushResult:
        calls["push"] += 1
        return PushResult(ok=True)

    monkeypatch.setattr("runners.run_command.commit", mock_commit)
    monkeypatch.setattr("runners.run_command.push", mock_push)

    from rich.console import Console
    console = Console(file=io.StringIO())
    _auto_commit_push(console, tmp_path, _snap_pushable(), _report_fixed(), no_push=True)
    assert calls["commit"] == 1
    assert calls["push"] == 0
