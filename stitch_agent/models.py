from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ErrorType(StrEnum):
    LINT = "lint"
    FORMAT = "format"
    SIMPLE_TYPE = "simple_type"
    CONFIG_CI = "config_ci"
    COMPLEX_TYPE = "complex_type"
    TEST_CONTRACT = "test_contract"
    LOGIC_ERROR = "logic_error"
    UNKNOWN = "unknown"


HAIKU_TYPES: frozenset[ErrorType] = frozenset(
    {
        ErrorType.LINT,
        ErrorType.FORMAT,
        ErrorType.SIMPLE_TYPE,
        ErrorType.CONFIG_CI,
    }
)

SONNET_TYPES: frozenset[ErrorType] = frozenset(
    {
        ErrorType.COMPLEX_TYPE,
        ErrorType.TEST_CONTRACT,
    }
)

ESCALATION_TYPES: frozenset[ErrorType] = frozenset(
    {
        ErrorType.LOGIC_ERROR,
        ErrorType.UNKNOWN,
    }
)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"


def select_model(error_type: ErrorType) -> str:
    if error_type in HAIKU_TYPES:
        return HAIKU_MODEL
    return SONNET_MODEL


class FixRequest(BaseModel):
    platform: Literal["gitlab", "github"]
    project_id: str
    pipeline_id: str
    job_id: str
    branch: str
    job_name: str | None = None


class ClassificationResult(BaseModel):
    error_type: ErrorType
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    affected_files: list[str] = Field(default_factory=list)


class FixResult(BaseModel):
    status: Literal["fixed", "escalate", "error"]
    error_type: ErrorType
    confidence: float = Field(ge=0.0, le=1.0)
    mr_url: str | None = None
    reason: str
    fix_branch: str | None = None
    escalation_reason_code: str | None = None


class StitchConfig(BaseModel):
    languages: list[str] = Field(default_factory=list)
    linter: str | None = None
    test_runner: str | None = None
    package_manager: str | None = None
    conventions: list[str] = Field(default_factory=list)
    auto_fix: list[str] = Field(
        default_factory=lambda: [
            "lint",
            "format",
            "simple_type",
            "config_ci",
            "complex_type",
            "test_contract",
        ]
    )
    escalate: list[str] = Field(default_factory=lambda: ["logic_errors", "breaking_changes"])
    notify: dict[str, str] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)
    docker_image: str | None = None
