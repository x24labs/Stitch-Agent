"""Tests for stitch_agent.run.filter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stitch_agent.run.filter import (
    FilterConfig,
    apply_filter,
    load_filter_config,
)
from stitch_agent.run.models import CIJob

if TYPE_CHECKING:
    from pathlib import Path


def _job(name: str) -> CIJob:
    return CIJob(name=name, stage="test", script=["echo"])


def test_default_skip_patterns_filter_deploy() -> None:
    jobs = [_job("lint"), _job("test"), _job("deploy-prod")]
    filtered = apply_filter(jobs, FilterConfig())
    assert filtered[0].skip_reason is None
    assert filtered[1].skip_reason is None
    assert filtered[2].skip_reason is not None
    assert "skip pattern" in filtered[2].skip_reason


def test_include_overrides_skip() -> None:
    jobs = [_job("deploy-staging")]
    cfg = FilterConfig(include=["deploy-staging"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None


def test_only_mode_excludes_others() -> None:
    jobs = [_job("lint"), _job("test"), _job("build")]
    cfg = FilterConfig(only=["lint"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None
    assert "allowlist" in (filtered[1].skip_reason or "")
    assert "allowlist" in (filtered[2].skip_reason or "")


def test_only_mode_prefix_match_colon() -> None:
    """`--jobs test` should match `test:unit`, `test:integration`, etc."""
    jobs = [
        _job("lint"),
        _job("test:unit"),
        _job("test:integration"),
        _job("build"),
    ]
    cfg = FilterConfig(only=["lint", "test"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None  # lint exact
    assert filtered[1].skip_reason is None  # test:unit prefix
    assert filtered[2].skip_reason is None  # test:integration prefix
    assert filtered[3].skip_reason is not None  # build excluded


def test_only_mode_prefix_match_dash_and_underscore() -> None:
    jobs = [_job("test-e2e"), _job("test_fast"), _job("testify")]
    cfg = FilterConfig(only=["test"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None  # test-e2e
    assert filtered[1].skip_reason is None  # test_fast
    # `testify` has no separator after `test`, so it should NOT match
    assert filtered[2].skip_reason is not None


def test_only_mode_exact_name_still_works() -> None:
    jobs = [_job("test:unit"), _job("test")]
    cfg = FilterConfig(only=["test:unit"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None  # exact match
    assert filtered[1].skip_reason is not None  # 'test' != 'test:unit'


def test_load_filter_config_missing_file(tmp_path: Path) -> None:
    cfg = load_filter_config(tmp_path)
    assert cfg.skip_patterns  # defaults
    assert cfg.include == []
    assert cfg.only is None


def test_load_filter_config_with_overrides(tmp_path: Path) -> None:
    (tmp_path / ".stitch.yml").write_text(
        """
run:
  include:
    - docker-build
  skip:
    - ^slow-
  only:
    - lint
    - test
"""
    )
    cfg = load_filter_config(tmp_path)
    assert "docker-build" in cfg.include
    assert any("slow-" in p for p in cfg.skip_patterns)
    assert cfg.only == ["lint", "test"]


def test_load_filter_config_malformed_yaml(tmp_path: Path) -> None:
    (tmp_path / ".stitch.yml").write_text("not: [valid\n")
    cfg = load_filter_config(tmp_path)
    assert cfg.only is None
    assert cfg.include == []
