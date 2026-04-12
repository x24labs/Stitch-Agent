"""AgentDriver protocol and shared prompt builder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from stitch_agent.run.models import FixContext, FixOutcome

_MAX_LOG_TAIL_CHARS = 12_000


@runtime_checkable
class AgentDriver(Protocol):
    """A driver that attempts to fix a failing local CI job.

    Implementations MUST NOT raise on fix failure. Return a FixOutcome with
    `applied=False` and a short `reason` instead.
    """

    name: str
    on_output: Callable[[str], None] | None

    async def fix(self, context: FixContext) -> FixOutcome: ...


def build_batch_prompt(contexts: list[FixContext]) -> str:
    """Build a single prompt covering multiple failing jobs at once."""
    per_job_chars = _MAX_LOG_TAIL_CHARS // len(contexts)
    parts: list[str] = [
        "Multiple local CI jobs failed. Fix ALL of them so every command passes.\n\n"
        "## Failing jobs\n\n",
    ]
    for i, ctx in enumerate(contexts, 1):
        log_tail = ctx.error_log[-per_job_chars:]
        parts.append(
            f"### {i}. {ctx.job_name}\n"
            f"Command: {ctx.command}\n"
            f"```\n{log_tail}\n```\n\n"
        )
    parts.append(
        "## Instructions\n"
        "- The failures above may share a common root cause, look for that first\n"
        "- Fix only what's needed to make ALL commands pass\n"
        "- Do not break other passing tests\n"
        "- If a failure requires environment changes and cannot be fixed in code, "
        "say so explicitly\n"
        "- When you believe all fixes are complete, stop and explain what you "
        "changed\n\n"
        f"Working directory: {contexts[0].repo_root}\n"
    )
    return "".join(parts)


def build_prompt(context: FixContext) -> str:
    """Build the textual prompt sent to CLI-based agent drivers."""
    if context.prompt_override is not None:
        return context.prompt_override
    log_tail = context.error_log[-_MAX_LOG_TAIL_CHARS:]
    return (
        "A local CI job failed. Fix it so the command passes.\n\n"
        "## Job\n"
        f"Name: {context.job_name}\n"
        f"Command: {context.command}\n"
        f"Attempt: {context.attempt}\n\n"
        "## Error output\n"
        "```\n"
        f"{log_tail}\n"
        "```\n\n"
        "## Instructions\n"
        "- Investigate by reading the relevant files\n"
        "- Fix only what's needed to make the command pass\n"
        "- Do not break other passing tests\n"
        "- If this failure requires environment changes (missing system "
        "package, external service) and cannot be fixed in code, say so "
        "explicitly and do not modify code\n"
        "- When you believe the fix is complete, stop and explain what you "
        "changed\n\n"
        f"Working directory: {context.repo_root}\n"
    )
