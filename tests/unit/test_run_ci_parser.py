"""Tests for stitch_agent.run.ci_parser."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stitch_agent.run.ci_parser import (
    CIParseError,
    _parse_github_workflow,
    _parse_gitlab_ci,
    parse_ci_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_gitlab_multi_stage(tmp_path: Path) -> None:
    yml = tmp_path / ".gitlab-ci.yml"
    yml.write_text(
        """
stages:
  - lint
  - test
  - deploy

lint-job:
  stage: lint
  script:
    - ruff check .

test-job:
  stage: test
  script:
    - pytest tests/

deploy-prod:
  stage: deploy
  script: echo "deploying"
"""
    )
    jobs = _parse_gitlab_ci(yml)
    assert [j.name for j in jobs] == ["lint-job", "test-job", "deploy-prod"]
    assert jobs[0].stage == "lint"
    assert jobs[0].script == ["ruff check ."]
    assert jobs[2].script == ['echo "deploying"']


def test_parse_gitlab_default_stage_is_test(tmp_path: Path) -> None:
    yml = tmp_path / ".gitlab-ci.yml"
    yml.write_text("only-job:\n  script: echo hi\n")
    jobs = _parse_gitlab_ci(yml)
    assert len(jobs) == 1
    assert jobs[0].stage == "test"


def test_parse_gitlab_ignores_hidden_and_reserved(tmp_path: Path) -> None:
    yml = tmp_path / ".gitlab-ci.yml"
    yml.write_text(
        """
image: python:3.12
variables:
  FOO: bar

.hidden-template:
  script: echo hidden

real-job:
  script: echo real
"""
    )
    jobs = _parse_gitlab_ci(yml)
    assert [j.name for j in jobs] == ["real-job"]
    assert jobs[0].image == "python:3.12"


def test_parse_gitlab_before_script_prepended(tmp_path: Path) -> None:
    yml = tmp_path / ".gitlab-ci.yml"
    yml.write_text(
        """
test:
  before_script:
    - pip install -e .
  script:
    - pytest
"""
    )
    jobs = _parse_gitlab_ci(yml)
    assert jobs[0].script == ["pip install -e .", "pytest"]


def test_parse_gitlab_script_as_string(tmp_path: Path) -> None:
    yml = tmp_path / ".gitlab-ci.yml"
    yml.write_text("lint:\n  script: ruff check .\n")
    jobs = _parse_gitlab_ci(yml)
    assert jobs[0].script == ["ruff check ."]


def test_parse_malformed_yaml_raises(tmp_path: Path) -> None:
    yml = tmp_path / ".gitlab-ci.yml"
    yml.write_text("invalid: [unclosed\n")
    with pytest.raises(CIParseError):
        _parse_gitlab_ci(yml)


def test_parse_github_workflow(tmp_path: Path) -> None:
    wf = tmp_path / "ci.yml"
    wf.write_text(
        """
name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: install
        run: pip install -e .
      - name: test
        run: pytest tests/
"""
    )
    jobs = _parse_github_workflow(wf)
    assert len(jobs) == 1
    assert jobs[0].name == "test"
    assert jobs[0].script == ["pip install -e .", "pytest tests/"]
    assert jobs[0].stage == "workflow:ci.yml"


def test_parse_github_skips_uses_only_jobs(tmp_path: Path) -> None:
    wf = tmp_path / "ci.yml"
    wf.write_text(
        """
jobs:
  checkout-only:
    steps:
      - uses: actions/checkout@v4
"""
    )
    assert _parse_github_workflow(wf) == []


def test_parse_ci_config_empty_repo(tmp_path: Path) -> None:
    assert parse_ci_config(tmp_path) == []


def test_parse_ci_config_both_platforms(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text("lint:\n  script: ruff check .\n")
    gh = tmp_path / ".github" / "workflows"
    gh.mkdir(parents=True)
    (gh / "ci.yml").write_text(
        """
jobs:
  build:
    steps:
      - run: make build
"""
    )
    jobs = parse_ci_config(tmp_path)
    names = [j.name for j in jobs]
    assert "lint" in names
    assert "build" in names
