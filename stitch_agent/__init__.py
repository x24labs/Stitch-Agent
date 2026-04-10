"""stitch-agent -- skill-first local CI runner."""

from stitch_agent.run import (
    CIJob,
    FixContext,
    FixOutcome,
    JobResult,
    RunReport,
)

__version__ = "1.0.0"

__all__ = [
    "CIJob",
    "FixContext",
    "FixOutcome",
    "JobResult",
    "RunReport",
    "__version__",
]
