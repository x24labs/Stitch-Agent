"""Tests for stitch_agent.run.filter."""

from __future__ import annotations

from stitch_agent.run.filter import FilterConfig, apply_filter
from stitch_agent.run.models import CIJob


def _job(name: str) -> CIJob:
    return CIJob(name=name, stage="test", script=["echo"])


def test_default_skip_patterns_filter_deploy() -> None:
    jobs = [_job("lint"), _job("test"), _job("deploy-prod")]
    filtered = apply_filter(jobs, FilterConfig())
    assert filtered[0].skip_reason is None
    assert filtered[1].skip_reason is None
    assert filtered[2].skip_reason is not None
    assert "skip pattern" in filtered[2].skip_reason


def test_only_mode_excludes_others() -> None:
    jobs = [_job("lint"), _job("test"), _job("build")]
    cfg = FilterConfig(only=["lint"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None
    assert "allowlist" in (filtered[1].skip_reason or "")
    assert "allowlist" in (filtered[2].skip_reason or "")


def test_only_mode_prefix_match_colon() -> None:
    jobs = [_job("lint"), _job("test:unit"), _job("test:integration"), _job("build")]
    cfg = FilterConfig(only=["lint", "test"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None
    assert filtered[1].skip_reason is None
    assert filtered[2].skip_reason is None
    assert filtered[3].skip_reason is not None


def test_only_mode_prefix_match_dash_and_underscore() -> None:
    jobs = [_job("test-e2e"), _job("test_fast"), _job("testify")]
    cfg = FilterConfig(only=["test"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None
    assert filtered[1].skip_reason is None
    assert filtered[2].skip_reason is not None


def test_only_mode_exact_name_still_works() -> None:
    jobs = [_job("test:unit"), _job("test")]
    cfg = FilterConfig(only=["test:unit"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None
    assert filtered[1].skip_reason is not None
