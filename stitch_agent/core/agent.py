from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx

from stitch_agent.config import parse_config
from stitch_agent.core.classifier import Classifier, _normalize_path
from stitch_agent.core.fixer import Fixer
from stitch_agent.core.patch_validator import PatchValidator
from stitch_agent.core.pr_creator import PRCreator
from stitch_agent.history import HistoryStore, default_db_path
from stitch_agent.models import (
    ESCALATION_TYPES,
    LIGHT_TYPES,
    ErrorType,
    FixRequest,
    FixResult,
    StitchConfig,
    UsageStats,
)

logger = logging.getLogger("stitch_agent")

if TYPE_CHECKING:
    from stitch_agent.adapters.base import CIPlatformAdapter


EscalationCallback = Callable[[FixRequest, FixResult], Awaitable[None]]

_OPENROUTER_GENERATION_URL = "https://openrouter.ai/api/v1/generation"


async def _fetch_generation_costs(
    api_key: str, usage: UsageStats, *, timeout: float = 5.0,
) -> None:
    """Fetch actual costs from OpenRouter for all tracked generation IDs."""
    if not usage.generation_ids:
        return
    async with httpx.AsyncClient(timeout=timeout) as client:
        for gen_id in usage.generation_ids:
            for _attempt in range(2):
                try:
                    resp = await client.get(
                        _OPENROUTER_GENERATION_URL,
                        params={"id": gen_id},
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if resp.status_code == 200:
                        cost = resp.json().get("data", {}).get("total_cost")
                        if cost is not None:
                            usage.cost_usd += float(cost)
                            break
                    # Stats may not be ready yet — brief delay before retry
                    await asyncio.sleep(0.3)
                except Exception:
                    break


class StitchAgent:
    def __init__(
        self,
        adapter: CIPlatformAdapter,
        anthropic_api_key: str = "",
        haiku_confidence_threshold: float = 0.80,
        sonnet_confidence_threshold: float = 0.40,
        max_attempts: int = 3,
        escalation_callback: EscalationCallback | None = None,
        history_store: HistoryStore | None = None,
        workspace_root: str = "/tmp/stitch-workspace",
        *,
        api_key: str = "",
        base_url: str | None = None,
    ) -> None:
        self.adapter = adapter
        self.haiku_confidence_threshold = haiku_confidence_threshold
        self.sonnet_confidence_threshold = sonnet_confidence_threshold
        self.max_attempts = max_attempts
        self.escalation_callback = escalation_callback
        # api_key takes precedence; fall back to anthropic_api_key for compat
        self._api_key = api_key or anthropic_api_key
        self._base_url = base_url
        self.classifier = Classifier(api_key=self._api_key, base_url=base_url)
        self.fixer = Fixer(self._api_key, base_url=base_url)
        self.pr_creator = PRCreator(adapter)
        self._history = history_store or HistoryStore(default_db_path(workspace_root))

    def _get_threshold(self, error_type: ErrorType) -> float:
        if error_type in LIGHT_TYPES:
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
        total_usage = UsageStats()
        total_usage += classification.usage

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
            normalized = _normalize_path(file_path)
            try:
                file_contents[normalized] = await self.adapter.fetch_file_content(request, normalized)
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
            adapter=self.adapter,
            request=request,
        )

        total_usage += fix_patch.usage

        logger.info(
            "fixer result: %d changes, commit_msg=%s, explanation=%s",
            len(fix_patch.changes),
            fix_patch.commit_message[:100],
            fix_patch.explanation[:200],
        )
        # Fetch actual costs from OpenRouter (non-blocking, best-effort)
        with contextlib.suppress(Exception):
            await _fetch_generation_costs(self._api_key, total_usage)

        logger.info(
            "usage: prompt=%d completion=%d total=%d tokens, cost=$%.6f",
            total_usage.prompt_tokens,
            total_usage.completion_tokens,
            total_usage.total_tokens,
            total_usage.cost_usd,
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

        # Validate CI config files before pushing (self-correction loop)
        ci_config_files = {".gitlab-ci.yml", ".github/workflows"}
        for _retry in range(2):
            ci_changes = [
                c for c in fix_patch.changes
                if any(c.path.endswith(cf) or c.path.startswith(".github/workflows") for cf in ci_config_files)
                if c.path == ".gitlab-ci.yml"
            ]
            if not ci_changes:
                break

            for change in ci_changes:
                valid, lint_error = await self.adapter.validate_ci_config(
                    request.project_id, change.new_content,
                )
                if not valid:
                    logger.warning("CI config lint failed: %s — asking LLM to fix", lint_error)
                    fix_patch = await self.fixer.generate_fix(
                        classification=classification,
                        job_log=f"PREVIOUS FIX PRODUCED INVALID CI CONFIG.\n"
                                f"GitLab CI lint error: {lint_error}\n\n"
                                f"Original job log:\n{job_log}",
                        diff=diff,
                        file_contents={change.path: change.new_content},
                        adapter=self.adapter,
                        request=request,
                    )
                    logger.info(
                        "self-correction result: %d changes", len(fix_patch.changes),
                    )
                    break  # re-validate
            else:
                break  # all CI configs valid

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
                usage=total_usage,
            )
            await self._notify(request, result, config)
            return result

        fix_id = request.pipeline_id
        fix_branch = f"stitch/fix-{fix_id}"
        changes = [{"path": c.path, "content": c.new_content} for c in fix_patch.changes]

        commit_message = (
            f"{fix_patch.commit_message}\n\nStitch-Target: {request.branch}"
        )

        # Try creating the branch; if it already exists (multi-job pipeline),
        # push an additional commit to the existing branch instead.
        try:
            fix_branch = await self.adapter.create_fix_branch(
                request=request,
                fix_id=fix_id,
                changes=changes,
                commit_message=commit_message,
            )
        except Exception:
            await self.adapter.push_to_branch(
                project_id=request.project_id,
                branch=fix_branch,
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
            usage=total_usage,
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

        # Model escalation: first attempts use classified model, later → heavy model
        model_override: str | None = None
        if attempt >= 2:
            model_override = config.models.heavy

        job_log = await self.adapter.fetch_job_logs(request)
        diff = await self.adapter.fetch_diff(request)
        classification = await self.classifier.classify(job_log, diff)

        file_contents: dict[str, str] = {}
        for file_path in classification.affected_files:
            normalized = _normalize_path(file_path)
            try:
                file_contents[normalized] = await self.adapter.fetch_file_content(
                    request, normalized
                )
            except Exception:
                continue

        fix_patch = await self.fixer.generate_fix(
            classification=classification,
            job_log=job_log,
            diff=diff,
            file_contents=file_contents,
            model_override=model_override,
            adapter=self.adapter,
            request=request,
            force_tools=True,
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
        self.classifier = Classifier(config=config, api_key=self._api_key, base_url=self._base_url)
        self.fixer = Fixer(self._api_key, config=config, base_url=self._base_url)
        return config
