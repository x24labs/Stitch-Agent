from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorType(StrEnum):
    LINT = "lint"
    FORMAT = "format"
    SIMPLE_TYPE = "simple_type"
    CONFIG_CI = "config_ci"
    COMPLEX_TYPE = "complex_type"
    TEST_CONTRACT = "test_contract"
    LOGIC_ERROR = "logic_error"
    BUILD = "build"
    UNKNOWN = "unknown"


LIGHT_TYPES: frozenset[ErrorType] = frozenset(
    {
        ErrorType.LINT,
        ErrorType.FORMAT,
        ErrorType.SIMPLE_TYPE,
        ErrorType.CONFIG_CI,
        ErrorType.BUILD,
    }
)

HEAVY_TYPES: frozenset[ErrorType] = frozenset(
    {
        ErrorType.COMPLEX_TYPE,
        ErrorType.TEST_CONTRACT,
        ErrorType.LOGIC_ERROR,
        ErrorType.UNKNOWN,
    }
)

ESCALATION_TYPES: frozenset[ErrorType] = frozenset()

# Kept as aliases for backward compatibility in tests/imports
HAIKU_TYPES = LIGHT_TYPES
SONNET_TYPES = HEAVY_TYPES

DEFAULT_CLASSIFIER_MODEL = "google/gemini-2.5-flash-lite"
DEFAULT_LIGHT_MODEL = "google/gemini-2.5-flash-lite"
DEFAULT_HEAVY_MODEL = "google/gemini-2.5-flash"

# Legacy aliases (used by retry_fix escalation)
HAIKU_MODEL = DEFAULT_LIGHT_MODEL
SONNET_MODEL = DEFAULT_HEAVY_MODEL


def select_model(error_type: ErrorType, light: str | None = None, heavy: str | None = None) -> str:
    if error_type in LIGHT_TYPES:
        return light or DEFAULT_LIGHT_MODEL
    return heavy or DEFAULT_HEAVY_MODEL


class FixRequest(BaseModel):
    platform: Literal["gitlab", "github"]
    project_id: str
    pipeline_id: str
    job_id: str
    branch: str
    job_name: str | None = None


class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __iadd__(self, other: UsageStats) -> UsageStats:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        return self


class ClassificationResult(BaseModel):
    error_type: ErrorType
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str
    affected_files: list[str] = Field(default_factory=list)
    usage: UsageStats = Field(default_factory=UsageStats)


class FixResult(BaseModel):
    status: Literal["fixed", "escalate", "error"]
    error_type: ErrorType
    confidence: float = Field(ge=0.0, le=1.0)
    mr_url: str | None = None
    reason: str
    fix_branch: str | None = None
    escalation_reason_code: str | None = None
    usage: UsageStats = Field(default_factory=UsageStats)


class NotifyChannelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    id: str | None = None
    url: str | None = None
    webhook_url: str | None = None


class NotifyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    fanout: Literal["parallel", "sequential"] = "parallel"
    channels: list[NotifyChannelConfig] | None = None
    webhook: str | None = None
    slack: str | None = None
    slack_webhook: str | None = None

    def has_destinations(self) -> bool:
        if self.channels is not None:
            return len(self.channels) > 0
        return any((self.webhook, self.slack, self.slack_webhook))


class ValidationConfig(BaseModel):
    enabled: bool = True
    max_diff_ratio: float = Field(default=0.40, ge=0.0, le=1.0)
    max_files_changed: int = Field(default=5, ge=1)
    max_lines_changed: int = Field(default=200, ge=1)
    block_new_imports: bool = True
    block_signature_changes: bool = True
    block_export_removal: bool = True


class PatchViolation(BaseModel):
    file_path: str
    check: str
    detail: str
    severity: Literal["error", "warning"] = "error"


class ValidationResult(BaseModel):
    passed: bool
    violations: list[PatchViolation] = Field(default_factory=list)


class ModelConfig(BaseModel):
    classifier: str = DEFAULT_CLASSIFIER_MODEL
    light: str = DEFAULT_LIGHT_MODEL
    heavy: str = DEFAULT_HEAVY_MODEL


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
            "build",
            "complex_type",
            "test_contract",
        ]
    )
    escalate: list[str] = Field(default_factory=lambda: ["logic_errors", "breaking_changes"])
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    max_attempts: int = Field(default=3, ge=1, le=10)
    models: ModelConfig = Field(default_factory=ModelConfig)
