from __future__ import annotations

import json

import pytest

from runners.cli import build_parser, parse_cli_args, run
from stitch_agent.onboarding.report import CommandReport


def test_parse_cli_args_keeps_fix_subcommand() -> None:
    parser = build_parser()
    args = parse_cli_args(
        parser,
        [
            "fix",
            "--platform",
            "gitlab",
            "--project-id",
            "42",
            "--pipeline-id",
            "100",
            "--job-id",
            "200",
            "--branch",
            "main",
        ],
    )
    assert args.command == "fix"


def test_parse_cli_args_supports_legacy_fix_invocation() -> None:
    parser = build_parser()
    args = parse_cli_args(
        parser,
        [
            "--platform",
            "gitlab",
            "--project-id",
            "42",
            "--pipeline-id",
            "100",
            "--job-id",
            "200",
            "--branch",
            "main",
        ],
    )
    assert args.command == "fix"


@pytest.mark.asyncio
async def test_doctor_json_output_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_run_doctor_checks(
        *, platform: str, repo_root: object, settings: object, project_id: str | None
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
