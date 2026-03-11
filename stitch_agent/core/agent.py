from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

from stitch_agent.config import parse_config
from stitch_agent.core.classifier import Classifier
from stitch_agent.core.fixer import Fixer
from stitch_agent.core.pr_creator import PRCreator
from stitch_agent.core.validator import Validator
from stitch_agent.history import HistoryStore, default_db_path
from stitch_agent.models import (
    ESCALATION_TYPES,
    HAIKU_TYPES,
    ErrorType,
    FixRequest,
    FixResult,
    StitchConfig,
)

if TYPE_CHECKING:
    from stitch_agent.adapters.base import CIPlatformAdapter

EscalationCallback = Callable[[FixRequest, FixResult], Awaitable[None]]


class StitchAgent:
    def __init__(
        self,
        adapter: CIPlatformAdapter,
        anthropic_api_key: str,
        haiku_confidence_threshold: float = 0.80,
        sonnet_confidence_threshold: float = 0.40,
        validation_mode: Literal["trusted", "strict"] = "trusted",
        max_attempts: int = 3,
        escalation_callback: EscalationCallback | None = None,
        history_store: HistoryStore | None = None,
        workspace_root: str = "/tmp/stitch-workspace",
    ) -> None:
        self.adapter = adapter
        self.haiku_confidence_threshold = haiku_confidence_threshold
        self.sonnet_confidence_threshold = sonnet_confidence_threshold
        self.max_attempts = max_attempts
        self.escalation_callback = escalation_callback
        self.classifier = Classifier()
        self.fixer = Fixer(anthropic_api_key)
        self.validator = Validator(mode=validation_mode)
        self.pr_creator = PRCreator(adapter)
        self._api_key = anthropic_api_key
        self._validation_mode: Literal["trusted", "strict"] = validation_mode
        self._history = history_store or HistoryStore(default_db_path(workspace_root))

    def _get_threshold(self, error_type: ErrorType) -> float:
        if error_type in HAIKU_TYPES:
            return self.haiku_confidence_threshold
        return self.sonnet_confidence_threshold

    def _escalate(
        self,
        error_type: ErrorType,
        confidence: float,
        reason: str,
        reason_code: str,
    ) -> FixResult:
        return FixResult(
            status="escalate",
            error_type=error_type,
            confidence=confidence,
            reason=reason,
            escalation_reason_code=reason_code,
        )

    async def fix(self, request: FixRequest) -> FixResult:
        result = await self._do_fix(request)
        with contextlib.suppress(Exception):
            self._history.record(request, result)
        return result

    async def _do_fix(self, request: FixRequest) -> FixResult:
        config = await self._load_repo_config(request)

        previous_attempts = await self.adapter.get_previous_fix_count(request)
        if previous_attempts >= self.max_attempts:
            result = self._escalate(
                ErrorType.UNKNOWN,
                0.0,
                f"Max attempts ({self.max_attempts}) reached for branch {request.branch}",
                "max_attempts_reached",
            )
            await self._notify(request, result, config)
            return result

        job_log = await self.adapter.fetch_job_logs(request)
        diff = await self.adapter.fetch_diff(request)

        classification = await self.classifier.classify(job_log, diff)

        if classification.error_type in ESCALATION_TYPES:
            result = self._escalate(
                classification.error_type,
                classification.confidence,
                classification.summary,
                "escalation_type",
            )
            await self._notify(request, result, config)
            return result

        threshold = self._get_threshold(classification.error_type)
        if classification.confidence < threshold:
            result = self._escalate(
                classification.error_type,
                classification.confidence,
                (f"Confidence {classification.confidence:.0%} below threshold {threshold:.0%}"),
                "low_confidence",
            )
            await self._notify(request, result, config)
            return result

        file_contents: dict[str, str] = {}
        for file_path in classification.affected_files:
            try:
                file_contents[file_path] = await self.adapter.fetch_file_content(request, file_path)
            except Exception:
                continue

        fix_patch = await self.fixer.generate_fix(
            classification=classification,
            job_log=job_log,
            diff=diff,
            file_contents=file_contents,
        )

        if not fix_patch.changes:
            result = FixResult(
                status="error",
                error_type=classification.error_type,
                confidence=classification.confidence,
                reason="Fixer produced no file changes",
                escalation_reason_code="no_changes",
            )
            await self._notify(request, result, config)
            return result

        if self._validation_mode == "strict":
            validation_result = await self._strict_validate(request, fix_patch.changes)
            if not validation_result.passed:
                result = self._escalate(
                    classification.error_type,
                    classification.confidence,
                    f"Strict validation failed: {validation_result.output[:500]}",
                    "validation_failed",
                )
                await self._notify(request, result, config)
                return result

        fix_id = request.pipeline_id
        changes = [{"path": c.path, "content": c.new_content} for c in fix_patch.changes]

        fix_branch = await self.adapter.create_fix_branch(
            request=request,
            fix_id=fix_id,
            changes=changes,
            commit_message=fix_patch.commit_message,
        )

        mr_url = await self.pr_creator.create(
            request=request,
            classification=classification,
            fix_branch=fix_branch,
            explanation=fix_patch.explanation,
        )

        result = FixResult(
            status="fixed",
            error_type=classification.error_type,
            confidence=classification.confidence,
            mr_url=mr_url,
            reason=fix_patch.explanation,
            fix_branch=fix_branch,
        )
        self._history.record(request, result)
        return result

    async def _strict_validate(
        self,
        request: FixRequest,
        changes: list,
    ):

        from stitch_agent.core.workspace import WorkspaceManager

        workspace = WorkspaceManager()
        clone_url = await self.adapter.get_clone_url(request)
        await workspace.ensure_clone(clone_url, request.project_id)
        worktree_path, _ = await workspace.create_worktree(request.project_id, request.branch)
        try:
            for change in changes:
                target = worktree_path / change.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(change.new_content)
            return await self.validator.validate(worktree_path)
        finally:
            await workspace.cleanup_worktree(request.project_id, worktree_path)

    async def _notify(
        self,
        request: FixRequest,
        result: FixResult,
        config: StitchConfig,
    ) -> None:
        if result.status == "fixed":
            return
        if self.escalation_callback:
            import contextlib

            with contextlib.suppress(Exception):
                await self.escalation_callback(request, result)
        if config.notify:
            from stitch_agent.core.notifier import Notifier

            notifier = Notifier(config)
            await notifier.notify_escalation(request, result)

    async def _load_repo_config(self, request: FixRequest) -> StitchConfig:
        raw = await self.adapter.get_repo_config(request)
        if raw is None:
            return StitchConfig()
        config = parse_config(raw)
        self.classifier = Classifier(config=config)
        self.fixer = Fixer(self._api_key, config=config)
        self.validator = Validator(mode=self._validation_mode, config=config)
        return config
