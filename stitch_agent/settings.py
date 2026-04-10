"""Environment-based settings for stitch (STITCH_* env vars)."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class StitchSettings(BaseSettings):
    model_config = {"env_prefix": "STITCH_"}

    gitlab_token: str = ""
    github_token: str = ""
    gitlab_base_url: str = "https://gitlab.com"
    github_base_url: str = "https://api.github.com"
