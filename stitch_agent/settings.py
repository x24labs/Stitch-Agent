from __future__ import annotations

from pydantic_settings import BaseSettings


class StitchSettings(BaseSettings):
    model_config = {"env_prefix": "STITCH_"}

    gitlab_token: str = ""
    github_token: str = ""
    anthropic_api_key: str = ""
    gitlab_base_url: str = "https://gitlab.com"
    github_base_url: str = "https://api.github.com"
    haiku_confidence_threshold: float = 0.80
    sonnet_confidence_threshold: float = 0.40
    max_attempts: int = 3
    workspace_root: str = "/tmp/stitch-workspace"
