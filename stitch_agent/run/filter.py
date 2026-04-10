"""Job filtering for stitch run.

Applies a default skip list to filter out jobs that are unsafe or unhelpful to
run locally (deploy, publish, docker-build, etc.). Users can override behavior
via a `.stitch.yml` file at the repo root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from stitch_agent.run.models import CIJob

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_SKIP_PATTERNS: list[str] = [
    r"^deploy",
    r"^publish",
    r"^release",
    r"^docker-build",
    r"^docker-push",
    r"^pages",
    r"^upload",
    r"^stitch",  # avoid recursion on stitch's own jobs
]


@dataclass
class FilterConfig:
    skip_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_SKIP_PATTERNS)
    )
    include: list[str] = field(default_factory=list)
    only: list[str] | None = None


def load_filter_config(repo_root: Path) -> FilterConfig:
    """Load filter config from .stitch.yml if present; else return defaults."""
    path = repo_root / ".stitch.yml"
    if not path.is_file():
        return FilterConfig()

    try:
        data: Any = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return FilterConfig()

    if not isinstance(data, dict):
        return FilterConfig()

    run_block = data.get("run")
    if not isinstance(run_block, dict):
        return FilterConfig()

    cfg = FilterConfig()
    skip = run_block.get("skip")
    if isinstance(skip, list):
        extras = [s for s in skip if isinstance(s, str)]
        cfg.skip_patterns = [*DEFAULT_SKIP_PATTERNS, *extras]

    include = run_block.get("include")
    if isinstance(include, list):
        cfg.include = [s for s in include if isinstance(s, str)]

    only = run_block.get("only")
    if isinstance(only, list):
        cfg.only = [s for s in only if isinstance(s, str)]

    return cfg


def _matches_allowlist(job_name: str, allowlist: list[str]) -> bool:
    """Return True if the job name matches any allowlist entry.

    Matching rules:
    - Exact match wins (e.g. "lint" matches job "lint")
    - Prefix match with common separators (e.g. "test" matches "test:unit",
      "test:integration", "test-e2e", "test_fast")
    - A bare entry is treated as a prefix to match colon/dash/underscore
      suffixed variants, so `--jobs test` catches `test:unit` naturally
    """
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
    """Annotate jobs with skip_reason. Does not remove entries from the list.

    Returns a new list with updated CIJob copies (preserving input order).
    """
    compiled = [re.compile(p) for p in cfg.skip_patterns]
    include_set = set(cfg.include)

    annotated: list[CIJob] = []
    for job in jobs:
        skip_reason: str | None = None

        if cfg.only is not None and not _matches_allowlist(job.name, cfg.only):
            skip_reason = f"not in --jobs allowlist {cfg.only!r}"
        elif job.name in include_set:
            skip_reason = None
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
