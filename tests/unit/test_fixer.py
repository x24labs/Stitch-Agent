from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stitch_agent.core.fixer import FileChange, Fixer, FixPatch, _parse_response
from stitch_agent.models import ClassificationResult, ErrorType


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
    from anthropic.types import TextBlock as ATextBlock

    mock_response = MagicMock()
    mock_response.content = [
        ATextBlock(
            type="text",
            text=json.dumps(
                {
                    "files": {"src/main.py": "fixed content\n"},
                    "commit_message": "fix(lint): remove F401",
                    "explanation": "Removed unused import.",
                }
            ),
        )
    ]

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        fixer = Fixer(anthropic_api_key="test-key")
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
async def test_generate_fix_selects_haiku_for_lint() -> None:
    from stitch_agent.models import HAIKU_MODEL

    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(text='{"files": {}, "commit_message": "fix: x", "explanation": "e"}')
    ]

    with patch("anthropic.AsyncAnthropic"):
        fixer = Fixer(anthropic_api_key="key")
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        fixer._client = mock_client

        await fixer.generate_fix(
            classification=_make_classification(ErrorType.LINT),
            job_log="error",
            diff="",
        )

    call_kwargs = mock_client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == HAIKU_MODEL


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
