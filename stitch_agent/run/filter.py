"""Job filtering for stitch run.

On first run, asks the LLM to classify each CI job as *verify* (code quality
checks to run locally) or *infra* (deploy, docker, packaging, etc.).  The
result is cached in ``.stitch/jobs.json`` so the LLM is only called when the
set of job names changes.  The ``--jobs`` flag bypasses classification entirely
and runs exactly the requested jobs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from stitch_agent.run.models import CIJob

# ---- cache ----------------------------------------------------------------

_CACHE_DIR = ".stitch"
_CACHE_FILE = "jobs.json"


def _cache_path(repo_root: Path) -> Path:
    return repo_root / _CACHE_DIR / _CACHE_FILE


def _job_names_hash(names: list[str]) -> str:
    """Deterministic hash of the sorted job name list."""
    return hashlib.sha256(
        "\n".join(sorted(names)).encode()
    ).hexdigest()[:16]


def load_cache(repo_root: Path, job_names: list[str]) -> dict[str, str] | None:
    """Load cached classification if it matches the current job set.

    Returns a dict of ``{job_name: "verify" | "infra"}`` or None.
    """
    path = _cache_path(repo_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if data.get("hash") != _job_names_hash(job_names):
        return None

    classifications = data.get("jobs")
    if not isinstance(classifications, dict):
        return None

    # Verify all current jobs are present
    if set(job_names) != set(classifications.keys()):
        return None

    return classifications


def save_cache(
    repo_root: Path,
    job_names: list[str],
    classifications: dict[str, str],
) -> None:
    """Persist classification to .stitch/jobs.json."""
    cache_dir = repo_root / _CACHE_DIR
    cache_dir.mkdir(exist_ok=True)
    _cache_path(repo_root).write_text(json.dumps({
        "hash": _job_names_hash(job_names),
        "jobs": classifications,
    }, indent=2) + "\n")


# ---- LLM classification --------------------------------------------------

_CLASSIFY_PROMPT = """\
Classify each CI job as either "verify" or "infra".

- **verify**: code quality jobs that make sense to run locally (lint, test, \
typecheck, build compilation, audit, format, check, coverage, analyze).
- **infra**: infrastructure jobs that should NOT run locally (deploy, docker \
build/push, publish, release, cleanup, sync, tag, migrate, seed, packaging \
artifacts like wheel builds, notifications, triggers).

Jobs:
{job_list}

Reply with ONLY a JSON object mapping each job name to "verify" or "infra". \
No markdown fences, no explanation. Example:
{{"lint": "verify", "test:unit": "verify", "deploy:prod": "infra"}}
"""


async def classify_with_llm(
    job_names: list[str],
    agent: str = "claude",
    repo_root: Path | None = None,
) -> dict[str, str] | None:
    """Ask the LLM to classify jobs. Returns {name: "verify"|"infra"} or None."""
    job_list = "\n".join(f"- {name}" for name in job_names)
    prompt = _CLASSIFY_PROMPT.format(job_list=job_list)

    if agent == "claude":
        output = await _call_claude(prompt, repo_root)
    elif agent == "codex":
        output = await _call_codex(prompt, repo_root)
    else:
        return None

    if output is None:
        return None

    return _parse_classification(output, job_names)


async def _call_claude(prompt: str, repo_root: Path | None) -> str | None:
    binary = "claude"
    if not shutil.which(binary):
        return None
    cmd = [binary, "-p", prompt, "--output-format", "text"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_root) if repo_root else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except (TimeoutError, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return (out or b"").decode("utf-8", errors="replace").strip()


async def _call_codex(prompt: str, repo_root: Path | None) -> str | None:
    binary = "codex"
    if not shutil.which(binary):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "exec", prompt,
            cwd=str(repo_root) if repo_root else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except (TimeoutError, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return (out or b"").decode("utf-8", errors="replace").strip()


def _parse_classification(
    raw: str, job_names: list[str],
) -> dict[str, str] | None:
    """Extract the JSON classification from LLM output."""
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the output
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    result: dict[str, str] = {}
    for name in job_names:
        val = data.get(name, "verify")
        result[name] = val if val in ("verify", "infra") else "verify"

    return result


# ---- public API -----------------------------------------------------------

@dataclass
class FilterConfig:
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


def apply_filter(
    jobs: list[CIJob],
    cfg: FilterConfig,
    classifications: dict[str, str] | None = None,
) -> list[CIJob]:
    """Annotate jobs with skip_reason.

    When *classifications* is provided (from cache or LLM), jobs classified as
    ``"infra"`` are skipped. When ``cfg.only`` is set, only those jobs run.
    When neither is available, all jobs run (no filtering).
    """
    from stitch_agent.run.models import CIJob as _CIJob

    annotated: list[_CIJob] = []
    for job in jobs:
        skip_reason: str | None = None

        if cfg.only is not None:
            if not _matches_allowlist(job.name, cfg.only):
                skip_reason = f"not in --jobs allowlist {cfg.only!r}"
        elif classifications:
            label = classifications.get(job.name, "verify")
            if label == "infra":
                skip_reason = "infrastructure job (classified by LLM)"

        annotated.append(
            _CIJob(
                name=job.name,
                stage=job.stage,
                script=job.script,
                image=job.image,
                source_file=job.source_file,
                skip_reason=skip_reason,
            )
        )
    return annotated
