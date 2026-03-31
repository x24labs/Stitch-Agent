from __future__ import annotations

from pydantic_settings import BaseSettings


class StitchSettings(BaseSettings):
    model_config = {"env_prefix": "STITCH_"}

    gitlab_token: str = ""
    github_token: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    gitlab_base_url: str = "https://gitlab.com"
    github_base_url: str = "https://api.github.com"
    haiku_confidence_threshold: float = 0.80
    sonnet_confidence_threshold: float = 0.40
    max_attempts: int = 3
    workspace_root: str = "/tmp/stitch-workspace"

    @property
    def llm_api_key(self) -> str:
        """Return the best available API key (OpenRouter preferred)."""
        return self.openrouter_api_key or self.anthropic_api_key

    @property
    def llm_base_url(self) -> str | None:
        """Return OpenRouter base URL if using OpenRouter, else None (Anthropic default)."""
        if self.openrouter_api_key:
            return self.openrouter_base_url
        return None
