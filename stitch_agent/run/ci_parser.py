"""Parse CI configuration files into a list of executable CIJob objects.

Supports:
- GitLab CI (.gitlab-ci.yml)
- GitHub Actions (.github/workflows/*.y?ml)

For the MVP we parse top-level jobs, normalize script into a list of shell
commands, and respect stage order. Advanced features (includes, `needs:`,
matrix strategies, `uses:` action steps) are deferred.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from stitch_agent.run.ci_detect import CIPlatform
from stitch_agent.run.models import CIJob

if TYPE_CHECKING:
    from pathlib import Path


class CIParseError(Exception):
    """Raised when a CI configuration file cannot be parsed."""


# Reserved top-level keys in GitLab CI that are not jobs.
_GITLAB_RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "default",
        "image",
        "include",
        "services",
        "stages",
        "variables",
        "workflow",
        "cache",
        "before_script",
        "after_script",
    }
)

_DEFAULT_GITLAB_STAGES: list[str] = [".pre", "build", "test", "deploy", ".post"]


def parse_ci_config(
    repo_root: Path,
    platform: CIPlatform = CIPlatform.UNKNOWN,
) -> list[CIJob]:
    """Parse CI files in the repo. Returns ordered jobs.

    When *platform* is a specific value (GITLAB or GITHUB), only that
    platform's config is parsed. When UNKNOWN, both are tried (original
    behaviour).

    Order: GitLab jobs first (if present), then GitHub workflow jobs sorted by
    filename. Within GitLab, jobs are ordered by stage. Within a stage, by
    insertion order from the YAML file.
    """
    jobs: list[CIJob] = []

    parse_gitlab = platform in (CIPlatform.GITLAB, CIPlatform.UNKNOWN)
    parse_github = platform in (CIPlatform.GITHUB, CIPlatform.UNKNOWN)

    if parse_gitlab:
        gl_path = repo_root / ".gitlab-ci.yml"
        if gl_path.is_file():
            jobs.extend(_parse_gitlab_ci(gl_path))

    if parse_github:
        gh_dir = repo_root / ".github" / "workflows"
        if gh_dir.is_dir():
            for wf in sorted(gh_dir.glob("*.yml")) + sorted(gh_dir.glob("*.yaml")):
                jobs.extend(_parse_github_workflow(wf))

    return jobs


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise CIParseError(f"Invalid YAML in {path}: {exc}") from exc


def _normalize_script(raw: Any) -> list[str]:
    """Convert a GitLab/GitHub script/run value into a list of shell commands."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, list):
                # nested list: flatten
                out.extend(x for x in item if isinstance(x, str))
        return out
    return []


def _parse_gitlab_ci(path: Path) -> list[CIJob]:
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return []

    stages_raw = data.get("stages")
    if isinstance(stages_raw, list) and stages_raw:
        stages_order = [s for s in stages_raw if isinstance(s, str)]
    else:
        stages_order = list(_DEFAULT_GITLAB_STAGES)

    default_image: str | None = None
    default_block = data.get("default")
    if isinstance(default_block, dict):
        img = default_block.get("image")
        if isinstance(img, str):
            default_image = img
        elif isinstance(img, dict) and isinstance(img.get("name"), str):
            default_image = img["name"]

    top_image = data.get("image")
    if default_image is None and isinstance(top_image, str):
        default_image = top_image
    elif (
        default_image is None
        and isinstance(top_image, dict)
        and isinstance(top_image.get("name"), str)
    ):
        default_image = top_image["name"]

    top_before_script = _normalize_script(data.get("before_script"))

    raw_jobs: list[tuple[str, dict[str, Any]]] = []
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        if key.startswith("."):
            # hidden template
            continue
        if key in _GITLAB_RESERVED_KEYS:
            continue
        if not isinstance(value, dict):
            continue
        if "script" not in value and "run" not in value:
            continue
        raw_jobs.append((key, value))

    # Group by stage in insertion order, then output in stages_order.
    by_stage: dict[str, list[CIJob]] = {}
    for name, block in raw_jobs:
        stage = block.get("stage") if isinstance(block.get("stage"), str) else "test"
        if stage not in stages_order:
            stages_order = [*stages_order, stage]

        job_image: str | None = default_image
        img = block.get("image")
        if isinstance(img, str):
            job_image = img
        elif isinstance(img, dict) and isinstance(img.get("name"), str):
            job_image = img["name"]

        before = _normalize_script(block.get("before_script")) or top_before_script
        script = _normalize_script(block.get("script"))
        if not script:
            script = _normalize_script(block.get("run"))

        full_script = [*before, *script]
        job = CIJob(
            name=name,
            stage=stage or "test",
            script=full_script,
            image=job_image,
            source_file=path.name,
        )
        by_stage.setdefault(job.stage, []).append(job)

    ordered: list[CIJob] = []
    for stage in stages_order:
        ordered.extend(by_stage.get(stage, []))
    # Any stages not in stages_order (shouldn't happen since we extend above,
    # but defensive)
    for stage, stage_jobs in by_stage.items():
        if stage not in stages_order:
            ordered.extend(stage_jobs)
    return ordered


def _parse_github_workflow(path: Path) -> list[CIJob]:
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return []

    jobs_block = data.get("jobs")
    if not isinstance(jobs_block, dict):
        return []

    stage = f"workflow:{path.name}"
    result: list[CIJob] = []
    for job_name, job_def in jobs_block.items():
        if not isinstance(job_name, str) or not isinstance(job_def, dict):
            continue

        steps = job_def.get("steps")
        script: list[str] = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                run = step.get("run")
                if isinstance(run, str):
                    script.append(run)
                elif isinstance(run, list):
                    script.extend(x for x in run if isinstance(x, str))

        if not script:
            # Jobs with only `uses:` steps are skipped by the parser.
            continue

        image: str | None = None
        container = job_def.get("container")
        if isinstance(container, str):
            image = container
        elif isinstance(container, dict) and isinstance(container.get("image"), str):
            image = container["image"]

        result.append(
            CIJob(
                name=job_name,
                stage=stage,
                script=script,
                image=image,
                source_file=path.name,
            )
        )
    return result
