from __future__ import annotations

import argparse
import asyncio
import json
import sys

from stitch_agent.adapters.gitlab import GitLabAdapter
from stitch_agent.core.agent import StitchAgent
from stitch_agent.models import FixRequest
from stitch_agent.settings import StitchSettings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stitch",
        description="The AI that stitches your CI back together",
    )
    p.add_argument("--platform", choices=["gitlab", "github"], default="gitlab")
    p.add_argument(
        "--project-id", required=True, help="GitLab: project ID or path. GitHub: owner/repo"
    )
    p.add_argument("--pipeline-id", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--job-name", default=None)
    p.add_argument("--gitlab-url", default=None, help="Override GitLab base URL")
    p.add_argument("--github-url", default=None, help="Override GitHub API base URL")
    p.add_argument(
        "--haiku-threshold",
        type=float,
        default=None,
        help="Confidence threshold for haiku types (default 0.80)",
    )
    p.add_argument(
        "--sonnet-threshold",
        type=float,
        default=None,
        help="Confidence threshold for sonnet types (default 0.40)",
    )
    p.add_argument("--output", choices=["json", "text"], default="text")
    return p


async def run(args: argparse.Namespace) -> int:
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
        validation_mode=settings.validation_mode,
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


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
