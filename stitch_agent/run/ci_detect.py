"""Auto-detect the CI platform from environment variables or config files.

When running inside a CI environment, the platform is identified by
well-known environment variables (GITLAB_CI, GITHUB_ACTIONS, etc.).
When running locally, the platform is inferred from which config files
exist in the repository root.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class CIPlatform(StrEnum):
    """Supported CI platforms."""

    GITLAB = "gitlab"
    GITHUB = "github"
    UNKNOWN = "unknown"


# Environment variable -> platform mapping.
# Checked in order; first match wins.
_ENV_SIGNALS: list[tuple[str, CIPlatform]] = [
    ("GITLAB_CI", CIPlatform.GITLAB),
    ("GITHUB_ACTIONS", CIPlatform.GITHUB),
]


def detect_platform(repo_root: Path | None = None) -> CIPlatform:
    """Detect the CI platform.

    1. Check environment variables (authoritative when running in CI).
    2. Fall back to config file presence in *repo_root*.
    3. Return UNKNOWN if nothing matches.
    """
    for env_var, platform in _ENV_SIGNALS:
        if os.environ.get(env_var):
            return platform

    if repo_root is not None:
        return _detect_from_files(repo_root)

    return CIPlatform.UNKNOWN


def _detect_from_files(repo_root: Path) -> CIPlatform:
    """Infer platform from CI config files on disk."""
    has_gitlab = (repo_root / ".gitlab-ci.yml").is_file()
    has_github = (repo_root / ".github" / "workflows").is_dir()

    if has_gitlab and not has_github:
        return CIPlatform.GITLAB
    if has_github and not has_gitlab:
        return CIPlatform.GITHUB

    # Both or neither: cannot disambiguate.
    return CIPlatform.UNKNOWN
