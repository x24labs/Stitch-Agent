"""CLI handler for `stitch generate <agent>`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from stitch_agent.run.repo_context import analyze_repo

if TYPE_CHECKING:
    import argparse

_MAX_CONTEXT_CHARS = 8_000


def _build_prompt(repo_root: Path) -> tuple[str, str]:
    """Build the LLM prompt. Returns (prompt, context_summary)."""
    ctx = analyze_repo(repo_root)
    summary = ctx.summary()

    # Read existing CI file for context (if any)
    ci_content = ""
    if ctx.existing_ci_file:
        ci_path = repo_root / ctx.existing_ci_file
        if ci_path.is_file():
            with contextlib.suppress(OSError):
                ci_content = ci_path.read_text()[:_MAX_CONTEXT_CHARS]

    # Read key config files for extra context
    config_snippets: list[str] = []
    for name in ctx.entry_files:
        if name.startswith("package.json scripts."):
            continue
        path = repo_root / name
        if path.is_file():
            try:
                content = path.read_text()[:2000]
                config_snippets.append(f"### {name}\n```\n{content}\n```")
            except OSError:
                pass

    platform = ctx.ci_platform or "gitlab"

    prompt_parts = [
        "Generate CI test/lint/check jobs for this repository.\n",
        "## Repository context\n",
        summary,
        "",
    ]

    if ci_content:
        prompt_parts.extend([
            "## Existing CI configuration\n",
            f"```yaml\n{ci_content}\n```\n",
            "Add ONLY the missing test/lint/check jobs. "
            "Preserve the existing structure, stages, and conventions.\n",
        ])
    else:
        prompt_parts.append(
            f"There is no CI configuration yet. Generate a complete "
            f"{platform} CI file with test, lint, and check stages.\n"
        )

    if config_snippets:
        prompt_parts.extend([
            "\n## Config files for reference\n",
            *config_snippets,
            "",
        ])

    prompt_parts.extend([
        "\n## Instructions\n",
        "- Output ONLY valid YAML for the CI configuration",
        f"- Target platform: {platform}",
        "- Include jobs for: lint, test, type checking (if applicable)",
        "- Use the project's actual tools and commands (from config files)",
        "- Keep jobs minimal and fast",
        "- Do NOT include deploy, docker, or infrastructure jobs",
        "- If the repo already has test jobs, say so and suggest improvements only",
        f"\nWorking directory: {repo_root}",
    ])

    return "\n".join(prompt_parts), summary


async def _call_claude(prompt: str, repo_root: Path, timeout: float) -> str | None:
    """Call Claude Code CLI in print mode and return the output."""
    binary = "claude"
    if not shutil.which(binary):
        print(f"Error: {binary} CLI not found in PATH", file=sys.stderr)
        return None

    proc = await asyncio.create_subprocess_exec(
        binary,
        "-p",
        prompt,
        "--output-format",
        "text",
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        print(f"Error: {binary} timed out after {timeout}s", file=sys.stderr)
        return None

    if proc.returncode != 0:
        print(f"Error: {binary} exited with code {proc.returncode}", file=sys.stderr)
        return None

    return (out or b"").decode("utf-8", errors="replace")


async def _call_codex(prompt: str, repo_root: Path, timeout: float) -> str | None:
    """Call Codex CLI and return the output."""
    binary = "codex"
    if not shutil.which(binary):
        print(f"Error: {binary} CLI not found in PATH", file=sys.stderr)
        return None

    proc = await asyncio.create_subprocess_exec(
        binary,
        "exec",
        prompt,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        print(f"Error: {binary} timed out after {timeout}s", file=sys.stderr)
        return None

    if proc.returncode != 0:
        print(f"Error: {binary} exited with code {proc.returncode}", file=sys.stderr)
        return None

    return (out or b"").decode("utf-8", errors="replace")


async def run_generate_command(args: argparse.Namespace) -> int:
    """Execute the generate command."""
    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"Error: repo path not found: {repo_root}", file=sys.stderr)
        return 2

    is_json = args.output == "json"
    console = Console(stderr=is_json)

    # Analyze repo
    prompt, summary = _build_prompt(repo_root)

    if args.dry_run:
        console.print(Panel(summary, title="Repository analysis", border_style="cyan"))
        console.print("\n[dim]Dry run: skipping LLM call[/]")
        if is_json:
            ctx = analyze_repo(repo_root)
            print(json.dumps({
                "languages": ctx.languages,
                "package_manager": ctx.package_manager,
                "frameworks": ctx.frameworks,
                "ci_platform": ctx.ci_platform,
                "has_test_jobs": ctx.has_test_jobs,
                "existing_ci_file": ctx.existing_ci_file,
            }, indent=2))
        return 0

    console.print(Panel(summary, title="Repository analysis", border_style="cyan"))
    console.print(f"\n[bold]Generating CI jobs with {args.agent}...[/]\n")

    # Call agent
    timeout = 120.0
    if args.agent == "claude":
        result = await _call_claude(prompt, repo_root, timeout)
    elif args.agent == "codex":
        result = await _call_codex(prompt, repo_root, timeout)
    else:
        print(f"Unknown agent: {args.agent}", file=sys.stderr)
        return 2

    if result is None:
        return 1

    if args.output == "json":
        print(json.dumps({"agent": args.agent, "generated": result}))
    else:
        console.print(Panel(result.strip(), title="Generated CI configuration", border_style="green"))

    return 0
