from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from stitch_agent.config import parse_config
from stitch_agent.core.classifier import Classifier
from stitch_agent.core.fixer import Fixer
from stitch_agent.core.pr_creator import PRCreator
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
        self.pr_creator = PRCreator(adapter)
        self._api_key = anthropic_api_key
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

    async def fix(self, request: FixRequest, *, create_mr: bool = True) -> FixResult:
        result = await self._do_fix(request, create_mr=create_mr)
        with contextlib.suppress(Exception):
            self._history.record(request, result)
        return result

    async def _do_fix(self, request: FixRequest, *, create_mr: bool = True) -> FixResult:
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

        fix_id = request.pipeline_id
        changes = [{"path": c.path, "content": c.new_content} for c in fix_patch.changes]

        commit_message = (
            f"{fix_patch.commit_message}\n\nStitch-Target: {request.branch}"
        )

        fix_branch = await self.adapter.create_fix_branch(
            request=request,
            fix_id=fix_id,
            changes=changes,
            commit_message=commit_message,
        )

        mr_url: str | None = None
        if create_mr:
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
        if config.notify.has_destinations():
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
        return config
