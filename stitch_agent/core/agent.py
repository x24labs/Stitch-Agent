from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from stitch_agent.config import parse_config
from stitch_agent.core.classifier import Classifier
from stitch_agent.core.fixer import Fixer
from stitch_agent.core.patch_validator import PatchValidator
from stitch_agent.core.pr_creator import PRCreator
from stitch_agent.history import HistoryStore, default_db_path
from stitch_agent.models import (
    ESCALATION_TYPES,
    HAIKU_TYPES,
    SONNET_MODEL,
    ErrorType,
    FixRequest,
    FixResult,
    StitchConfig,
)

logger = logging.getLogger("stitch_agent")

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

        logger.debug(
            "job_log length: %d chars, %d lines",
            len(job_log), job_log.count("\n"),
        )

        classification = await self.classifier.classify(job_log, diff)

        logger.info(
            "classification: type=%s confidence=%.0f%% affected_files=%s summary=%s",
            classification.error_type.value,
            classification.confidence * 100,
            classification.affected_files[:10],
            classification.summary[:200],
        )

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
        fetch_failed: list[str] = []
        for file_path in classification.affected_files:
            try:
                file_contents[file_path] = await self.adapter.fetch_file_content(request, file_path)
            except Exception:
                fetch_failed.append(file_path)
                continue

        logger.info(
            "file_contents: fetched=%d failed=%s",
            len(file_contents),
            fetch_failed or "(none)",
        )

        fix_patch = await self.fixer.generate_fix(
            classification=classification,
            job_log=job_log,
            diff=diff,
            file_contents=file_contents,
        )

        logger.info(
            "fixer result: %d changes, commit_msg=%s, explanation=%s",
            len(fix_patch.changes),
            fix_patch.commit_message[:100],
            fix_patch.explanation[:200],
        )

        # Validate patch before pushing — reject destructive fixes
        validator = PatchValidator(config.validation)
        validation = validator.validate(fix_patch, file_contents, classification.error_type)
        if not validation.passed:
            detail = "; ".join(
                f"{v.check}: {v.detail}" for v in validation.violations[:5]
            )
            logger.warning("patch validation failed: %s", detail)
            result = self._escalate(
                classification.error_type,
                classification.confidence,
                f"Patch rejected by validation: {detail}",
                "patch_validation_failed",
            )
            await self._notify(request, result, config)
            return result

        if not fix_patch.changes:
            logger.warning(
                "fixer returned no changes for job=%s type=%s",
                request.job_name or request.job_id,
                classification.error_type.value,
            )
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

    async def retry_fix(
        self,
        request: FixRequest,
        fix_branch: str,
        target_branch: str,
        attempt: int,
    ) -> FixResult:
        """Retry a failed fix on an existing stitch/fix-* branch.

        Escalates to a stronger model after initial attempts fail.
        """
        config = await self._load_repo_config(request)

        # Model escalation: first attempts use classified model, later → Sonnet
        model_override: str | None = None
        if attempt >= 2:
            model_override = SONNET_MODEL

        job_log = await self.adapter.fetch_job_logs(request)
        diff = await self.adapter.fetch_diff(request)
        classification = await self.classifier.classify(job_log, diff)

        file_contents: dict[str, str] = {}
        for file_path in classification.affected_files:
            try:
                file_contents[file_path] = await self.adapter.fetch_file_content(
                    request, file_path
                )
            except Exception:
                continue

        fix_patch = await self.fixer.generate_fix(
            classification=classification,
            job_log=job_log,
            diff=diff,
            file_contents=file_contents,
            model_override=model_override,
        )

        # Validate patch
        validator = PatchValidator(config.validation)
        validation = validator.validate(fix_patch, file_contents, classification.error_type)
        if not validation.passed:
            detail = "; ".join(
                f"{v.check}: {v.detail}" for v in validation.violations[:5]
            )
            return self._escalate(
                classification.error_type,
                classification.confidence,
                f"Retry patch rejected by validation: {detail}",
                "patch_validation_failed",
            )

        if not fix_patch.changes:
            return self._escalate(
                classification.error_type,
                classification.confidence,
                "Retry fixer produced no file changes",
                "no_changes",
            )

        changes = [{"path": c.path, "content": c.new_content} for c in fix_patch.changes]
        commit_message = (
            f"{fix_patch.commit_message}\n\n"
            f"Stitch-Target: {target_branch}\n"
            f"Stitch-Retry: {attempt}"
        )

        await self.adapter.push_to_branch(
            project_id=request.project_id,
            branch=fix_branch,
            changes=changes,
            commit_message=commit_message,
        )

        model_used = model_override or "auto"
        return FixResult(
            status="fixed",
            error_type=classification.error_type,
            confidence=classification.confidence,
            reason=(
                f"Retry #{attempt}: {fix_patch.explanation} "
                f"(model: {model_used})"
            ),
            fix_branch=fix_branch,
        )

    async def _load_repo_config(self, request: FixRequest) -> StitchConfig:
        raw = await self.adapter.get_repo_config(request)
        if raw is None:
            return StitchConfig()
        config = parse_config(raw)
        self.classifier = Classifier(config=config)
        self.fixer = Fixer(self._api_key, config=config)
        return config
