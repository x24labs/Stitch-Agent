"""Agent drivers for stitch run.

An AgentDriver is responsible for attempting to fix a failing local CI job by
editing files in the repository. Drivers may spawn an external CLI (Claude Code,
Codex) that uses the user's subscription, or fall back to the internal API-based
Fixer (ApiDriver).
"""

from stitch_agent.run.drivers.api import ApiDriver
from stitch_agent.run.drivers.base import AgentDriver, build_prompt
from stitch_agent.run.drivers.claude_code import ClaudeCodeDriver
from stitch_agent.run.drivers.codex import CodexDriver

__all__ = [
    "AgentDriver",
    "ApiDriver",
    "ClaudeCodeDriver",
    "CodexDriver",
    "build_prompt",
]
