from __future__ import annotations

import logging

import httpx
import pytest
import respx

from stitch_agent.core.notifier import Notifier
from stitch_agent.models import (
    ErrorType,
    FixRequest,
    FixResult,
    NotifyChannelConfig,
    NotifyConfig,
    StitchConfig,
)

pytestmark = pytest.mark.asyncio


def _make_request() -> FixRequest:
    return FixRequest(
        platform="gitlab",
        project_id="42",
        pipeline_id="100",
        job_id="200",
        branch="main",
    )


def _make_result(reason_code: str = "low_confidence") -> FixResult:
    return FixResult(
        status="escalate",
        error_type=ErrorType.LINT,
        confidence=0.5,
        reason="Low confidence",
        escalation_reason_code=reason_code,
    )


@respx.mock
async def test_notify_webhook() -> None:
    respx.post("https://hooks.example.com/stitch").mock(return_value=httpx.Response(200))
    config = StitchConfig(notify=NotifyConfig(webhook="https://hooks.example.com/stitch"))
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 1
    call = respx.calls.last
    body = call.request.content
    import json

    payload = json.loads(body)
    assert payload["event"] == "escalation"
    assert payload["reason_code"] == "low_confidence"


@respx.mock
async def test_notify_slack() -> None:
    respx.post("https://hooks.slack.com/services/xxx").mock(return_value=httpx.Response(200))
    config = StitchConfig(notify=NotifyConfig(slack="https://hooks.slack.com/services/xxx"))
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 1
    call = respx.calls.last
    import json

    payload = json.loads(call.request.content)
    assert "stitch escalation" in payload["text"]


@respx.mock
async def test_notify_both_webhook_and_slack() -> None:
    respx.post("https://hooks.example.com/stitch").mock(return_value=httpx.Response(200))
    respx.post("https://hooks.slack.com/services/yyy").mock(return_value=httpx.Response(200))
    config = StitchConfig(
        notify=NotifyConfig(
            webhook="https://hooks.example.com/stitch",
            slack="https://hooks.slack.com/services/yyy",
        )
    )
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 2


async def test_notify_no_config_does_nothing() -> None:
    config = StitchConfig(notify=NotifyConfig())
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())


@respx.mock
async def test_notify_webhook_failure_is_silent() -> None:
    respx.post("https://hooks.example.com/broken").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    config = StitchConfig(notify=NotifyConfig(webhook="https://hooks.example.com/broken"))
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())


@respx.mock
async def test_notify_structured_channels() -> None:
    respx.post("https://hooks.example.com/structured").mock(return_value=httpx.Response(200))
    respx.post("https://hooks.slack.com/services/structured").mock(return_value=httpx.Response(200))
    config = StitchConfig(
        notify=NotifyConfig(
            channels=[
                NotifyChannelConfig(type="webhook", url="https://hooks.example.com/structured"),
                NotifyChannelConfig(
                    type="slack",
                    webhook_url="https://hooks.slack.com/services/structured",
                ),
            ]
        )
    )
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 2


@respx.mock
async def test_structured_channels_override_legacy() -> None:
    respx.post("https://hooks.example.com/from-channels").mock(return_value=httpx.Response(200))
    respx.post("https://hooks.example.com/from-legacy").mock(return_value=httpx.Response(200))
    config = StitchConfig(
        notify=NotifyConfig(
            channels=[
                NotifyChannelConfig(type="webhook", url="https://hooks.example.com/from-channels")
            ],
            webhook="https://hooks.example.com/from-legacy",
        )
    )
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 1
    assert str(respx.calls.last.request.url) == "https://hooks.example.com/from-channels"


@respx.mock
async def test_legacy_notify_mapping_from_dict_config() -> None:
    respx.post("https://hooks.example.com/legacy").mock(return_value=httpx.Response(200))
    config = StitchConfig.model_validate(
        {"notify": {"webhook": "https://hooks.example.com/legacy"}}
    )
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 1


@respx.mock
async def test_unknown_channel_type_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    respx.post("https://hooks.example.com/valid").mock(return_value=httpx.Response(200))
    config = StitchConfig(
        notify=NotifyConfig(
            channels=[
                NotifyChannelConfig(type="teams", url="https://example.com/teams"),
                NotifyChannelConfig(type="webhook", url="https://hooks.example.com/valid"),
            ]
        )
    )
    notifier = Notifier(config)
    with caplog.at_level(logging.WARNING, logger="stitch.notifier"):
        await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 1
    assert "Unsupported notify channel type 'teams'" in caplog.text
