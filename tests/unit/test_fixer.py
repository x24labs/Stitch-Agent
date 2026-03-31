from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from stitch_agent.core.fixer import FileChange, Fixer, FixPatch, _extract_usage, _parse_response
from stitch_agent.models import ClassificationResult, ErrorType, UsageStats


def _make_classification(
    error_type: ErrorType = ErrorType.LINT,
    confidence: float = 0.95,
    affected_files: list[str] | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        error_type=error_type,
        confidence=confidence,
        summary=f"[{error_type.value}] test error",
        affected_files=affected_files or ["src/foo.py"],
    )


def _mock_openai_response(
    text: str,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    generation_id: str = "gen-test-123",
) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response with usage."""
    message = MagicMock()
    message.content = text
    message.tool_calls = None
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    response.id = generation_id
    return response


def test_parse_response_valid_json() -> None:
    data = {
        "files": {"src/foo.py": "x = 1\n"},
        "commit_message": "fix(lint): remove unused import",
        "explanation": "Removed unused import of os.",
    }
    result = _parse_response(json.dumps(data))
    assert len(result.changes) == 1
    assert result.changes[0].path == "src/foo.py"
    assert result.changes[0].new_content == "x = 1\n"
    assert result.commit_message == "fix(lint): remove unused import"
    assert "Removed" in result.explanation


def test_parse_response_json_in_fence() -> None:
    data = {"files": {"a.py": "pass\n"}, "commit_message": "fix: test", "explanation": "ok"}
    raw = f"Here is the fix:\n```json\n{json.dumps(data)}\n```"
    result = _parse_response(raw)
    assert result.changes[0].path == "a.py"


def test_parse_response_invalid_json() -> None:
    result = _parse_response("sorry, I cannot fix this")
    assert result.changes == []
    assert "Could not parse" in result.explanation


def test_parse_response_missing_fields() -> None:
    result = _parse_response('{"files": {}}')
    assert result.changes == []
    assert result.commit_message == "fix: automated fix by stitch-agent"


def test_parse_response_non_string_values_ignored() -> None:
    raw = json.dumps(
        {
            "files": {"a.py": 123, "b.py": "content\n"},
            "commit_message": "fix: x",
            "explanation": "e",
        }
    )
    result = _parse_response(raw)
    assert len(result.changes) == 1
    assert result.changes[0].path == "b.py"


@pytest.mark.asyncio
async def test_generate_fix_calls_api_and_returns_patch() -> None:
    fix_json = json.dumps({
        "files": {"src/main.py": "fixed content\n"},
        "commit_message": "fix(lint): remove F401",
        "explanation": "Removed unused import.",
    })
    mock_response = _mock_openai_response(fix_json)

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    fixer = Fixer(api_key="test-key")
    fixer._client = mock_client

    classification = _make_classification(ErrorType.LINT)
    result = await fixer.generate_fix(
        classification=classification,
        job_log="src/main.py:1:1: F401 unused import",
        diff="--- a/src/main.py\n+++ b/src/main.py\n",
    )

    assert isinstance(result, FixPatch)
    assert result.changes[0].path == "src/main.py"
    assert result.commit_message == "fix(lint): remove F401"


@pytest.mark.asyncio
async def test_generate_fix_selects_light_model_for_lint() -> None:
    from stitch_agent.models import DEFAULT_LIGHT_MODEL

    mock_response = _mock_openai_response(
        '{"files": {}, "commit_message": "fix: x", "explanation": "e"}'
    )

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    fixer = Fixer(api_key="key")
    fixer._client = mock_client

    await fixer.generate_fix(
        classification=_make_classification(ErrorType.LINT),
        job_log="error",
        diff="",
    )

    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["model"] == DEFAULT_LIGHT_MODEL


def test_file_change_default_action() -> None:
    fc = FileChange(path="foo.py", new_content="x = 1\n")
    assert fc.action == "update"


def test_smart_truncate_preserves_errors() -> None:
    from stitch_agent.core.fixer import _smart_truncate_log

    lines = ["setup line"] * 50
    lines += ["FAILED tests/test_foo.py::test_bar - AssertionError: x != y"]
    lines += ["E       assert 1 == 2"]
    lines += ["normal line"] * 200
    lines += ["1 failed, 10 passed"]
    log = "\n".join(lines)

    result = _smart_truncate_log(log, max_lines=80)
    assert "FAILED tests/test_foo.py" in result
    assert "assert 1 == 2" in result
    assert "1 failed, 10 passed" in result
    assert result.count("\n") < len(lines)


def test_smart_truncate_short_log_unchanged() -> None:
    from stitch_agent.core.fixer import _smart_truncate_log

    log = "line 1\nline 2\nline 3"
    assert _smart_truncate_log(log) == log


# --- Usage tracking tests ---


def test_parse_response_single_quoted_python_dict() -> None:
    """Models sometimes return Python-style dicts with single quotes."""
    raw = "{'files': {'src/foo.py': 'import os\\n'}, 'commit_message': 'fix: remove unused', 'explanation': 'Removed unused import'}"
    result = _parse_response(raw)
    assert len(result.changes) == 1
    assert result.changes[0].path == "src/foo.py"
    assert result.commit_message == "fix: remove unused"


def test_extract_usage_with_tokens_and_id() -> None:
    response = _mock_openai_response("text", prompt_tokens=200, completion_tokens=80, generation_id="gen-abc")
    usage = _extract_usage(response)
    assert usage.prompt_tokens == 200
    assert usage.completion_tokens == 80
    assert usage.total_tokens == 280
    assert usage.generation_ids == ["gen-abc"]


def test_extract_usage_no_usage_attr() -> None:
    response = MagicMock(spec=[])  # no attributes
    usage = _extract_usage(response)
    assert usage.prompt_tokens == 0
    assert usage.total_tokens == 0
    assert usage.generation_ids == []


def test_extract_usage_no_generation_id() -> None:
    response = _mock_openai_response("text", generation_id="gen-xyz")
    response.id = None
    usage = _extract_usage(response)
    assert usage.prompt_tokens == 100
    assert usage.generation_ids == []


def test_usage_stats_accumulation() -> None:
    a = UsageStats(prompt_tokens=100, completion_tokens=50, total_tokens=150, cost_usd=0.001, generation_ids=["gen-1"])
    b = UsageStats(prompt_tokens=200, completion_tokens=80, total_tokens=280, cost_usd=0.002, generation_ids=["gen-2"])
    a += b
    assert a.prompt_tokens == 300
    assert a.completion_tokens == 130
    assert a.total_tokens == 430
    assert a.cost_usd == pytest.approx(0.003)
    assert a.generation_ids == ["gen-1", "gen-2"]


def test_usage_stats_empty_accumulation() -> None:
    a = UsageStats()
    b = UsageStats()
    a += b
    assert a.prompt_tokens == 0
    assert a.cost_usd == 0.0
    assert a.generation_ids == []


@pytest.mark.asyncio
async def test_generate_fix_captures_usage() -> None:
    fix_json = json.dumps({
        "files": {"src/main.py": "fixed\n"},
        "commit_message": "fix: test",
        "explanation": "fixed it",
    })
    mock_response = _mock_openai_response(fix_json, prompt_tokens=500, completion_tokens=200, generation_id="gen-fix-1")

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    fixer = Fixer(api_key="test-key")
    fixer._client = mock_client

    result = await fixer.generate_fix(
        classification=_make_classification(ErrorType.LINT),
        job_log="error",
        diff="",
        file_contents={"src/main.py": "broken\n"},
    )

    assert result.usage.prompt_tokens == 500
    assert result.usage.completion_tokens == 200
    assert result.usage.total_tokens == 700
    assert "gen-fix-1" in result.usage.generation_ids


@pytest.mark.asyncio
async def test_fetch_generation_costs_mocked() -> None:
    from unittest.mock import patch

    from stitch_agent.core.agent import _fetch_generation_costs

    usage = UsageStats(prompt_tokens=100, completion_tokens=50, total_tokens=150, generation_ids=["gen-abc", "gen-def"])

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": {"total_cost": 0.0042}}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("stitch_agent.core.agent.httpx.AsyncClient", return_value=mock_client):
        await _fetch_generation_costs("fake-key", usage)

    assert usage.cost_usd == pytest.approx(0.0084)  # 0.0042 * 2 generation IDs
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_generation_costs_skips_on_empty_ids() -> None:
    from stitch_agent.core.agent import _fetch_generation_costs

    usage = UsageStats(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    await _fetch_generation_costs("fake-key", usage)
    assert usage.cost_usd == 0.0


def test_print_text_results_shows_usage(capsys: pytest.CaptureFixture[str]) -> None:
    from runners.ci_runner import _print_text_results

    results: list[dict[str, object]] = [{
        "job_name": "lint",
        "status": "fixed",
        "reason": "Removed unused import",
        "fix_branch": "stitch/fix-123",
        "usage": {
            "prompt_tokens": 1234,
            "completion_tokens": 567,
            "total_tokens": 1801,
            "cost_usd": 0.0003,
        },
    }]
    _print_text_results(results)
    out = capsys.readouterr().out
    assert "1,234 in" in out
    assert "567 out" in out
    assert "1,801 total" in out
    assert "$0.0003" in out


def test_print_text_results_no_cost(capsys: pytest.CaptureFixture[str]) -> None:
    from runners.ci_runner import _print_text_results

    results: list[dict[str, object]] = [{
        "job_name": "lint",
        "status": "fixed",
        "reason": "Fixed",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cost_usd": 0},
    }]
    _print_text_results(results)
    out = capsys.readouterr().out
    assert "100 in" in out
    assert "$" not in out  # no cost shown when 0


def test_print_text_results_capitalizes_status(capsys: pytest.CaptureFixture[str]) -> None:
    from runners.ci_runner import _print_text_results

    results: list[dict[str, object]] = [{"job_name": "test", "status": "fixed", "reason": "ok"}]
    _print_text_results(results)
    out = capsys.readouterr().out
    assert "Fixed" in out
