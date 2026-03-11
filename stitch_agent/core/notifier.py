from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from stitch_agent.models import FixRequest, FixResult, StitchConfig

logger = logging.getLogger("stitch.notifier")


class Notifier:
    def __init__(self, config: StitchConfig) -> None:
        self.config = config

    async def notify_escalation(self, request: FixRequest, result: FixResult) -> None:
        webhook_url = self.config.notify.get("webhook")
        slack_url = self.config.notify.get("slack")
        if webhook_url:
            await self._post_webhook(webhook_url, request, result)
        if slack_url:
            await self._post_slack(slack_url, request, result)

    async def _post_webhook(self, url: str, request: FixRequest, result: FixResult) -> None:
        payload = {
            "event": "escalation",
            "project_id": request.project_id,
            "pipeline_id": request.pipeline_id,
            "branch": request.branch,
            "error_type": result.error_type.value,
            "confidence": result.confidence,
            "reason": result.reason,
            "reason_code": result.escalation_reason_code,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json=payload)
        except Exception:
            logger.warning("Failed to post escalation webhook to %s", url)

    async def _post_slack(self, url: str, request: FixRequest, result: FixResult) -> None:
        text = (
            f":warning: *stitch escalation* \u2014 `{request.project_id}` / `{request.branch}`\n"
            f"Error: `{result.error_type.value}` | Confidence: {result.confidence:.0%}\n"
            f"Reason: {result.reason}"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, json={"text": text})
        except Exception:
            logger.warning("Failed to post escalation Slack message to %s", url)
