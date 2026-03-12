from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CheckSeverity = Literal["error", "warning", "info"]
CheckStatus = Literal["pass", "fail", "warn", "skip"]


@dataclass(slots=True)
class CheckResult:
    id: str
    status: CheckStatus
    severity: CheckSeverity
    message: str
    remediation: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(slots=True)
class CommandReport:
    command: str
    ok: bool
    schema_version: str = "1.0"
    actions_taken: list[str] = field(default_factory=list)
    actions_skipped: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "command": self.command,
            "ok": self.ok,
            "actions_taken": self.actions_taken,
            "actions_skipped": self.actions_skipped,
            "prompts": self.prompts,
            "warnings": self.warnings,
            "errors": self.errors,
            "next_steps": self.next_steps,
            "checks": [check.to_dict() for check in self.checks],
        }

    def exit_code(self) -> int:
        if self.prompts:
            return 2
        return 0 if self.ok else 1


def build_command_report(
    *,
    command: str,
    checks: list[CheckResult],
    actions_taken: list[str] | None = None,
    actions_skipped: list[str] | None = None,
    prompts: list[str] | None = None,
    next_steps: list[str] | None = None,
) -> CommandReport:
    warnings: list[str] = []
    errors: list[str] = []
    merged_next_steps: list[str] = list(next_steps or [])
    prompts = list(prompts or [])

    for check in checks:
        if check.status == "fail" and check.severity == "error":
            errors.append(f"{check.id}: {check.message}")
        elif check.status in {"warn", "fail"} and check.severity == "warning":
            warnings.append(f"{check.id}: {check.message}")

        if (
            check.remediation
            and check.status in {"fail", "warn", "skip"}
            and check.remediation not in merged_next_steps
        ):
            merged_next_steps.append(check.remediation)

    ok = not errors and not prompts

    return CommandReport(
        command=command,
        ok=ok,
        actions_taken=list(actions_taken or []),
        actions_skipped=list(actions_skipped or []),
        prompts=prompts,
        warnings=warnings,
        errors=errors,
        next_steps=merged_next_steps,
        checks=checks,
    )
