"""Agent drivers for stitch run.

An AgentDriver spawns an external CLI (Claude Code, Codex) that uses the
user's existing subscription to investigate and fix failing CI jobs.
"""

from stitch_agent.run.drivers.base import AgentDriver, build_prompt
from stitch_agent.run.drivers.claude_code import ClaudeCodeDriver
from stitch_agent.run.drivers.codex import CodexDriver

__all__ = [
    "AgentDriver",
    "ClaudeCodeDriver",
    "CodexDriver",
    "build_prompt",
]
