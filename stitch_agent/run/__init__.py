"""stitch run — local CI runner with AI fix loop.

This package provides the `stitch run <agent>` command surface: parse CI YAML,
execute jobs locally, and delegate fix attempts to an AI agent CLI (Claude Code,
Codex) or the existing API-based Fixer.
"""

from stitch_agent.run.models import (
    CIJob,
    FixContext,
    FixOutcome,
    JobResult,
    RunReport,
)

__all__ = [
    "CIJob",
    "FixContext",
    "FixOutcome",
    "JobResult",
    "RunReport",
]
