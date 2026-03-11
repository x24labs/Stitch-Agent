from __future__ import annotations

from typing import Literal

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
    validation_mode: Literal["trusted", "strict"] = "trusted"
    max_attempts: int = 3
    workspace_root: str = "/tmp/stitch-workspace"
    webhook_secret: str = ""
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8000
    webhook_api_keys: str = ""
    webhook_rate_limit: int = 60
    webhook_rate_window: int = 60
