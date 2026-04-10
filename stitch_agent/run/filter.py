"""Job filtering for stitch run.

Applies a default skip list to filter out jobs that are unsafe or unhelpful to
run locally (deploy, publish, docker-build, etc.). All filtering is controlled
via CLI flags (--jobs, --skip). No config files needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stitch_agent.run.models import CIJob

DEFAULT_SKIP_PATTERNS: list[str] = [
    r"^deploy",
    r"^publish",
    r"^release",
    r"^docker-build",
    r"^docker-push",
    r"^pages",
    r"^upload",
    r"^stitch",
]


@dataclass
class FilterConfig:
    skip_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_SKIP_PATTERNS)
    )
    only: list[str] | None = None


def _matches_allowlist(job_name: str, allowlist: list[str]) -> bool:
    """Exact match or prefix match with separators (: - _)."""
    separators = (":", "-", "_")
    for entry in allowlist:
        if job_name == entry:
            return True
        if (
            job_name.startswith(entry)
            and len(job_name) > len(entry)
            and job_name[len(entry)] in separators
        ):
            return True
    return False


def apply_filter(jobs: list[CIJob], cfg: FilterConfig) -> list[CIJob]:
    """Annotate jobs with skip_reason based on CLI flags and default skip list."""
    compiled = [re.compile(p) for p in cfg.skip_patterns]

    annotated: list[CIJob] = []
    for job in jobs:
        skip_reason: str | None = None

        if cfg.only is not None and not _matches_allowlist(job.name, cfg.only):
            skip_reason = f"not in --jobs allowlist {cfg.only!r}"
        else:
            for pat in compiled:
                if pat.search(job.name):
                    skip_reason = f"matches skip pattern {pat.pattern!r}"
                    break

        annotated.append(
            CIJob(
                name=job.name,
                stage=job.stage,
                script=job.script,
                image=job.image,
                source_file=job.source_file,
                skip_reason=skip_reason,
            )
        )
    return annotated
