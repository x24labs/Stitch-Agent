from __future__ import annotations

import httpx
import pytest
import respx

from stitch_agent.core.notifier import Notifier
from stitch_agent.models import ErrorType, FixRequest, FixResult, StitchConfig

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
    config = StitchConfig(notify={"webhook": "https://hooks.example.com/stitch"})
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
    config = StitchConfig(notify={"slack": "https://hooks.slack.com/services/xxx"})
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
        notify={
            "webhook": "https://hooks.example.com/stitch",
            "slack": "https://hooks.slack.com/services/yyy",
        }
    )
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
    assert respx.calls.call_count == 2


async def test_notify_no_config_does_nothing() -> None:
    config = StitchConfig(notify={})
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())


@respx.mock
async def test_notify_webhook_failure_is_silent() -> None:
    respx.post("https://hooks.example.com/broken").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    config = StitchConfig(notify={"webhook": "https://hooks.example.com/broken"})
    notifier = Notifier(config)
    await notifier.notify_escalation(_make_request(), _make_result())
