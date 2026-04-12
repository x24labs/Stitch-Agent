"""AgentDriver protocol and shared prompt builder."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
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


def build_prompt(context: FixContext) -> str:
    """Build the textual prompt sent to CLI-based agent drivers."""
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
