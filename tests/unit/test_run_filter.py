"""Tests for stitch_agent.run.filter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from stitch_agent.run.filter import (
    FilterConfig,
    _job_names_hash,
    _parse_classification,
    apply_filter,
    load_cache,
    save_cache,
)
from stitch_agent.run.models import CIJob

if TYPE_CHECKING:
    from pathlib import Path


def _job(name: str, stage: str = "test") -> CIJob:
    return CIJob(name=name, stage=stage, script=["echo"])


# ---------------------------------------------------------------------------
# apply_filter with --jobs (only)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# apply_filter with LLM classifications
# ---------------------------------------------------------------------------


def test_classification_skips_infra() -> None:
    jobs = [
        _job("lint", stage="check"),
        _job("test:unit", stage="check"),
        _job("docker:build", stage="docker"),
        _job("deploy:prod", stage="deploy"),
    ]
    classifications = {
        "lint": "verify",
        "test:unit": "verify",
        "docker:build": "infra",
        "deploy:prod": "infra",
    }
    filtered = apply_filter(jobs, FilterConfig(), classifications=classifications)
    assert filtered[0].skip_reason is None
    assert filtered[1].skip_reason is None
    assert filtered[2].skip_reason is not None
    assert "infrastructure" in filtered[2].skip_reason
    assert filtered[3].skip_reason is not None


def test_no_classifications_runs_all() -> None:
    """Without classifications or --jobs, all jobs run."""
    jobs = [_job("lint"), _job("docker:build")]
    filtered = apply_filter(jobs, FilterConfig())
    assert all(j.skip_reason is None for j in filtered)


def test_only_overrides_classifications() -> None:
    """--jobs takes priority over LLM classifications."""
    jobs = [_job("docker:build", stage="docker")]
    classifications = {"docker:build": "infra"}
    cfg = FilterConfig(only=["docker"])
    filtered = apply_filter(jobs, cfg, classifications=classifications)
    assert filtered[0].skip_reason is None


# ---------------------------------------------------------------------------
# _parse_classification
# ---------------------------------------------------------------------------


def test_parse_clean_json() -> None:
    raw = '{"lint": "verify", "deploy": "infra"}'
    result = _parse_classification(raw, ["lint", "deploy"])
    assert result == {"lint": "verify", "deploy": "infra"}


def test_parse_with_markdown_fences() -> None:
    raw = '```json\n{"lint": "verify", "deploy": "infra"}\n```'
    result = _parse_classification(raw, ["lint", "deploy"])
    assert result == {"lint": "verify", "deploy": "infra"}


def test_parse_with_extra_text() -> None:
    raw = 'Here is the classification:\n{"lint": "verify", "deploy": "infra"}\nDone.'
    result = _parse_classification(raw, ["lint", "deploy"])
    assert result == {"lint": "verify", "deploy": "infra"}


def test_parse_missing_job_defaults_to_verify() -> None:
    raw = '{"lint": "verify"}'
    result = _parse_classification(raw, ["lint", "test"])
    assert result is not None
    assert result["lint"] == "verify"
    assert result["test"] == "verify"


def test_parse_invalid_returns_none() -> None:
    assert _parse_classification("not json at all", ["lint"]) is None


def test_parse_invalid_value_defaults_to_verify() -> None:
    raw = '{"lint": "banana"}'
    result = _parse_classification(raw, ["lint"])
    assert result is not None
    assert result["lint"] == "verify"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_save_and_load_cache(tmp_path: Path) -> None:
    names = ["lint", "test", "deploy"]
    classifications = {"lint": "verify", "test": "verify", "deploy": "infra"}
    save_cache(tmp_path, names, classifications)

    loaded = load_cache(tmp_path, names)
    assert loaded == classifications


def test_cache_invalidated_on_job_change(tmp_path: Path) -> None:
    names_v1 = ["lint", "test"]
    save_cache(tmp_path, names_v1, {"lint": "verify", "test": "verify"})

    names_v2 = ["lint", "test", "deploy"]
    assert load_cache(tmp_path, names_v2) is None


def test_cache_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_cache(tmp_path, ["lint"]) is None


def test_cache_returns_none_on_corrupt_file(tmp_path: Path) -> None:
    cache_dir = tmp_path / ".stitch"
    cache_dir.mkdir()
    (cache_dir / "jobs.json").write_text("not json")
    assert load_cache(tmp_path, ["lint"]) is None


def test_hash_is_order_independent() -> None:
    h1 = _job_names_hash(["lint", "test", "deploy"])
    h2 = _job_names_hash(["deploy", "lint", "test"])
    assert h1 == h2
