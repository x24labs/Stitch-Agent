from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from stitch_agent.models import FixRequest, FixResult, NotifyChannelConfig, StitchConfig

logger = logging.getLogger("stitch.notifier")


@dataclass(slots=True)
class EscalationEvent:
    request: FixRequest
    result: FixResult

    def webhook_payload(self) -> dict[str, str | float | None]:
        return {
            "event": "escalation",
            "project_id": self.request.project_id,
            "pipeline_id": self.request.pipeline_id,
            "branch": self.request.branch,
            "error_type": self.result.error_type.value,
            "confidence": self.result.confidence,
            "reason": self.result.reason,
            "reason_code": self.result.escalation_reason_code,
        }

    def slack_payload(self) -> dict[str, str]:
        text = (
            f":warning: *stitch escalation* - `{self.request.project_id}` / `{self.request.branch}`\n"
            f"Error: `{self.result.error_type.value}` | Confidence: {self.result.confidence:.0%}\n"
            f"Reason: {self.result.reason}"
        )
        return {"text": text}


class NotificationChannel(Protocol):
    type: str

    async def send(self, client: httpx.AsyncClient, event: EscalationEvent) -> None: ...


@dataclass(slots=True)
class GenericWebhookChannel:
    url: str
    channel_id: str | None = None
    type: str = "webhook"

    async def send(self, client: httpx.AsyncClient, event: EscalationEvent) -> None:
        await client.post(self.url, json=event.webhook_payload())


@dataclass(slots=True)
class SlackWebhookChannel:
    url: str
    channel_id: str | None = None
    type: str = "slack"

    async def send(self, client: httpx.AsyncClient, event: EscalationEvent) -> None:
        await client.post(self.url, json=event.slack_payload())


def _endpoint_hint(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or "unknown-host"


class Notifier:
    def __init__(self, config: StitchConfig) -> None:
        self.config = config

    async def notify_escalation(self, request: FixRequest, result: FixResult) -> None:
        channels = self._build_channels()
        if not channels:
            return

        event = EscalationEvent(request=request, result=result)
        timeout = self.config.notify.timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            if self.config.notify.fanout == "sequential":
                for channel in channels:
                    await self._send_channel(channel, client, event)
                return

            await asyncio.gather(
                *(self._send_channel(channel, client, event) for channel in channels),
                return_exceptions=True,
            )

    async def _send_channel(
        self,
        channel: NotificationChannel,
        client: httpx.AsyncClient,
        event: EscalationEvent,
    ) -> None:
        try:
            await channel.send(client, event)
        except Exception:
            logger.warning(
                "Failed to deliver escalation notification",
                extra={
                    "channel_type": channel.type,
                    "project_id": event.request.project_id,
                    "pipeline_id": event.request.pipeline_id,
                    "reason_code": event.result.escalation_reason_code,
                },
            )

    def _build_channels(self) -> list[NotificationChannel]:
        channels_config = self.config.notify.channels
        if channels_config is not None and len(channels_config) > 0:
            return self._from_structured_config(channels_config)
        if channels_config is not None:
            return []
        return self._from_legacy_config()

    def _from_structured_config(
        self,
        channels_config: list[NotifyChannelConfig],
    ) -> list[NotificationChannel]:
        channels: list[NotificationChannel] = []
        for index, channel in enumerate(channels_config):
            built = self._build_single_channel(
                channel.type, channel.url, channel.webhook_url, channel.id
            )
            if built is None:
                logger.warning("Skipping invalid notify channel at index %s", index)
                continue
            channels.append(built)
        return channels

    def _from_legacy_config(self) -> list[NotificationChannel]:
        channels: list[NotificationChannel] = []
        webhook = self.config.notify.webhook
        slack = self.config.notify.slack or self.config.notify.slack_webhook
        if webhook:
            channels.append(GenericWebhookChannel(url=webhook, channel_id="legacy-webhook"))
        if slack:
            channels.append(SlackWebhookChannel(url=slack, channel_id="legacy-slack"))
        return channels

    def _build_single_channel(
        self,
        channel_type: str,
        url: str | None,
        webhook_url: str | None,
        channel_id: str | None,
    ) -> NotificationChannel | None:
        normalized = channel_type.lower().strip()
        if normalized == "webhook":
            target = url or webhook_url
            if not target:
                return None
            return GenericWebhookChannel(url=target, channel_id=channel_id)
        if normalized == "slack":
            target = webhook_url or url
            if not target:
                return None
            return SlackWebhookChannel(url=target, channel_id=channel_id)

        logger.warning("Unsupported notify channel type '%s'", normalized)
        if url:
            logger.debug("Channel endpoint host: %s", _endpoint_hint(url))
        if webhook_url:
            logger.debug("Channel endpoint host: %s", _endpoint_hint(webhook_url))
        return None
