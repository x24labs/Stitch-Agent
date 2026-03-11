from __future__ import annotations

import pytest

from stitch_agent.core.classifier import Classifier
from stitch_agent.models import ErrorType

pytestmark = pytest.mark.asyncio


@pytest.fixture
def clf() -> Classifier:
    return Classifier()


async def test_lint_flake8(clf: Classifier) -> None:
    log = (
        "Running ruff check...\n"
        "src/auth.py:10:5: F401 'os' imported but unused\n"
        "src/auth.py:15:1: E302 expected 2 blank lines, found 1\n"
        "Found 2 errors.\n"
    )
    result = await clf.classify(log)
    assert result.error_type == ErrorType.LINT
    assert result.confidence > 0.7
    assert any("auth.py" in f for f in result.affected_files)


async def test_format_black(clf: Classifier) -> None:
    log = (
        "--- reformatting ---\nwould reformat src/models.py\nOh no! 1 file would be reformatted.\n"
    )
    result = await clf.classify(log)
    assert result.error_type == ErrorType.FORMAT
    assert result.confidence > 0.7


async def test_format_isort(clf: Classifier) -> None:
    log = "isort: ERROR: src/utils.py Imports are incorrectly sorted and/or formatted.\n"
    result = await clf.classify(log)
    assert result.error_type == ErrorType.FORMAT


async def test_simple_type_mypy(clf: Classifier) -> None:
    log = (
        "src/service.py:42: error: Argument 1 to 'process' has incompatible type 'str'; expected 'int'\n"
        "src/service.py:50: error: Incompatible types in assignment "
        "(expression has type 'list[str]', variable has type 'list[int]')\n"
        "src/service.py:55: error: Item 'None' of 'Optional[str]' has no attribute 'upper'\n"
        "Found 3 errors in 1 file (checked 5 source files)\n"
    )
    result = await clf.classify(log)
    assert result.error_type == ErrorType.SIMPLE_TYPE
    assert result.confidence > 0.7


async def test_config_ci_gitlab(clf: Classifier) -> None:
    log = (
        "Validating .gitlab-ci.yml ...\n"
        "ci configuration is invalid: yaml syntax error at line 12\n"
        "Please fix the pipeline configuration.\n"
    )
    result = await clf.classify(log)
    assert result.error_type == ErrorType.CONFIG_CI
    assert result.confidence > 0.7


async def test_logic_error_traceback(clf: Classifier) -> None:
    log = (
        "Traceback (most recent call last):\n"
        "  File 'app.py', line 10, in <module>\n"
        "    result = divide(10, 0)\n"
        "ZeroDivisionError: division by zero\n"
    )
    result = await clf.classify(log)
    assert result.error_type == ErrorType.LOGIC_ERROR


async def test_test_contract_pytest(clf: Classifier) -> None:
    log = (
        "FAILED tests/test_api.py::test_create_user - AssertionError: assert 400 == 201\n"
        "1 failed, 5 passed in 0.45s\n"
    )
    result = await clf.classify(log)
    assert result.error_type == ErrorType.TEST_CONTRACT


async def test_unknown_empty_log(clf: Classifier) -> None:
    result = await clf.classify("Build succeeded\nAll checks passed\n")
    assert result.error_type == ErrorType.UNKNOWN
    assert result.confidence == 0.5


async def test_summary_contains_type(clf: Classifier) -> None:
    log = "src/foo.py:1:1: F401 'sys' imported but unused\n"
    result = await clf.classify(log)
    assert result.error_type.value in result.summary


async def test_affected_files_extracted(clf: Classifier) -> None:
    log = (
        "src/auth/models.py:10: error: Incompatible types in assignment\n"
        "src/auth/views.py:5: error: Incompatible types in assignment\n"
    )
    result = await clf.classify(log)
    assert any("models.py" in f for f in result.affected_files)
    assert any("views.py" in f for f in result.affected_files)


async def test_confidence_is_between_0_and_1(clf: Classifier) -> None:
    log = "src/main.py:1:1: E501 line too long\n"
    result = await clf.classify(log)
    assert 0.0 <= result.confidence <= 1.0
