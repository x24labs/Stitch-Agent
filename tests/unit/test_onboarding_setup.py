from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from stitch_agent.onboarding.setup import run_setup

if TYPE_CHECKING:
    from pathlib import Path


def test_setup_creates_config_with_detected_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip()
        + "\n"
    )
    (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - test\n")
    (tmp_path / "tests").mkdir()

    report = run_setup(repo_root=tmp_path, platform="gitlab")

    assert report.exit_code() == 0
    assert report.ok is True
    assert any("Created .stitch.yml" in action for action in report.actions_taken)

    payload = yaml.safe_load((tmp_path / ".stitch.yml").read_text())
    assert payload["languages"] == ["python"]
    assert payload["linter"] == "ruff"
    assert payload["test_runner"] == "pytest"
    assert payload["package_manager"] == "pip"


def test_setup_is_idempotent_when_config_is_current(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip()
        + "\n"
    )
    (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - test\n")
    (tmp_path / "tests").mkdir()

    first = run_setup(repo_root=tmp_path, platform="gitlab")
    second = run_setup(repo_root=tmp_path, platform="gitlab")

    assert first.exit_code() == 0
    assert second.exit_code() == 0
    assert second.actions_taken == []
    assert any("Configuration already up-to-date" in action for action in second.actions_skipped)
