"""Tests for runners/cli.py (v1.0 -- run, setup, doctor subcommands only)."""

from __future__ import annotations

import json

import pytest

from runners.cli import build_parser, parse_cli_args, run
from stitch_agent.onboarding.report import CommandReport


def test_parse_run_subcommand() -> None:
    parser = build_parser()
    args = parse_cli_args(parser, ["run", "claude", "--dry-run"])
    assert args.command == "run"
    assert args.agent == "claude"
    assert args.dry_run is True


def test_parse_setup_subcommand() -> None:
    parser = build_parser()
    args = parse_cli_args(parser, ["setup", "--platform", "github"])
    assert args.command == "setup"
    assert args.platform == "github"


def test_parse_doctor_subcommand() -> None:
    parser = build_parser()
    args = parse_cli_args(parser, ["doctor", "--json"])
    assert args.command == "doctor"
    assert args.json is True


def test_no_subcommand_exits_nonzero() -> None:
    parser = build_parser()
    args = parse_cli_args(parser, [])
    assert args.command is None


@pytest.mark.asyncio
async def test_doctor_json_output_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run_doctor_checks(
        *, platform: str, repo_root: object, settings: object, project_id: str | None,
    ) -> CommandReport:
        assert platform == "gitlab"
        assert project_id is None
        return CommandReport(
            command="doctor",
            ok=False,
            actions_taken=[],
            actions_skipped=[],
            prompts=["Needs manual auth"],
            warnings=["One warning"],
            errors=["One error"],
            next_steps=["Run stitch connect"],
            checks=[],
        )

    monkeypatch.setattr("runners.cli.run_doctor_checks", fake_run_doctor_checks)

    parser = build_parser()
    args = parse_cli_args(parser, ["doctor", "--platform", "gitlab", "--json"])
    code = await run(args)
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 2
    for key in [
        "schema_version",
        "command",
        "ok",
        "actions_taken",
        "actions_skipped",
        "prompts",
        "warnings",
        "errors",
        "next_steps",
        "checks",
    ]:
        assert key in payload
