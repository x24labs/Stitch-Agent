"""Stitch configuration model loaded from .stitch.yml."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StitchConfig(BaseModel):
    languages: list[str] = Field(default_factory=list)
    linter: str | None = None
    test_runner: str | None = None
    package_manager: str | None = None
    conventions: list[str] = Field(default_factory=list)
