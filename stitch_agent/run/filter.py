"""Job filtering for stitch run.

Classifies CI jobs as *verification* (code quality checks that make sense
locally) or *infrastructure* (deploy, docker, cleanup, etc.) and only runs
verification jobs by default.  The ``--jobs`` flag overrides classification
and runs exactly the requested jobs.

Classification uses both job name and stage name against known patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from stitch_agent.run.models import CIJob

# Patterns that identify code-verification jobs (match against name or stage).
VERIFY_PATTERNS: list[str] = [
    r"lint",
    r"test",
    r"check",
    r"typecheck",
    r"type.check",
    r"format",
    r"audit",
    r"build",
    r"style",
    r"coverage",
    r"analyze",
    r"validate",
]

# Patterns that identify infrastructure / non-verification jobs.
INFRA_PATTERNS: list[str] = [
    r"deploy",
    r"publish",
    r"release",
    r"docker",
    r"image",
    r"container",
    r"push",
    r"pages",
    r"upload",
    r"cleanup",
    r"clean",
    r"migrate",
    r"seed",
    r"backup",
    r"notify",
    r"trigger",
    r"stitch",
]

_VERIFY_RE = [re.compile(p, re.IGNORECASE) for p in VERIFY_PATTERNS]
_INFRA_RE = [re.compile(p, re.IGNORECASE) for p in INFRA_PATTERNS]


def _earliest_match(text: str, patterns: list[re.Pattern[str]]) -> int | None:
    """Return the earliest start position of any pattern match, or None."""
    best: int | None = None
    for pat in patterns:
        m = pat.search(text)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def _first_infra_pattern(text: str) -> re.Pattern[str]:
    """Return the first infra pattern that matches *text*."""
    return next(p for p in _INFRA_RE if p.search(text))


def classify_job(job: CIJob) -> str | None:
    """Return a skip reason if the job is not a verification job, else None.

    The job *name* is the primary signal. When both verify and infra
    patterns match the name, the one appearing earliest wins (e.g.
    ``docker:build`` -> ``docker`` at pos 0 beats ``build`` at pos 7).
    The *stage* is a secondary hint.

    Fallback: unknown jobs run (benefit of the doubt).
    """
    name_verify_pos = _earliest_match(job.name, _VERIFY_RE)
    name_infra_pos = _earliest_match(job.name, _INFRA_RE)

    # Both match the name: earliest position wins.
    if name_verify_pos is not None and name_infra_pos is not None:
        if name_infra_pos < name_verify_pos:
            pat = _first_infra_pattern(job.name)
            return f"infrastructure job (matches '{pat.pattern}')"
        return None

    if name_verify_pos is not None:
        return None

    if name_infra_pos is not None:
        pat = _first_infra_pattern(job.name)
        return f"infrastructure job (matches '{pat.pattern}')"

    # Name is ambiguous, fall back to stage.
    if any(p.search(job.stage) for p in _VERIFY_RE):
        return None

    if any(p.search(job.stage) for p in _INFRA_RE):
        pat = next(p for p in _INFRA_RE if p.search(job.stage))
        return f"infrastructure job (matches '{pat.pattern}')"

    return None


@dataclass
class FilterConfig:
    only: list[str] | None = None
    auto_classify: bool = True


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
    """Annotate jobs with skip_reason based on classification or --jobs flag."""
    annotated: list[CIJob] = []
    for job in jobs:
        skip_reason: str | None = None

        if cfg.only is not None:
            if not _matches_allowlist(job.name, cfg.only):
                skip_reason = f"not in --jobs allowlist {cfg.only!r}"
        elif cfg.auto_classify:
            skip_reason = classify_job(job)

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
