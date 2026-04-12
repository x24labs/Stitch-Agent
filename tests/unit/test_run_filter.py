"""Tests for stitch_agent.run.filter."""

from __future__ import annotations

from stitch_agent.run.filter import FilterConfig, apply_filter, classify_job
from stitch_agent.run.models import CIJob


def _job(name: str, stage: str = "test", script: list[str] | None = None) -> CIJob:
    return CIJob(name=name, stage=stage, script=script or ["echo"])


# ---------------------------------------------------------------------------
# classify_job
# ---------------------------------------------------------------------------


class TestClassifyJob:
    def test_lint_is_verification(self) -> None:
        assert classify_job(_job("lint", stage="check")) is None

    def test_test_unit_is_verification(self) -> None:
        assert classify_job(_job("test:unit", stage="check")) is None

    def test_audit_is_verification(self) -> None:
        assert classify_job(_job("audit", stage="check")) is None

    def test_build_is_verification(self) -> None:
        assert classify_job(_job("build", stage="build")) is None

    def test_typecheck_is_verification(self) -> None:
        assert classify_job(_job("typecheck", stage="check")) is None

    def test_docker_build_is_infra(self) -> None:
        reason = classify_job(_job("docker:build", stage="docker"))
        assert reason is not None
        assert "infrastructure" in reason

    def test_deploy_is_infra(self) -> None:
        reason = classify_job(_job("deploy:dokploy", stage="deploy"))
        assert reason is not None
        assert "infrastructure" in reason

    def test_cleanup_images_is_infra(self) -> None:
        reason = classify_job(_job("cleanup:images", stage="cleanup"))
        assert reason is not None
        assert "infrastructure" in reason

    def test_publish_is_infra(self) -> None:
        reason = classify_job(_job("publish-npm", stage="release"))
        assert reason is not None

    def test_verify_pattern_wins_over_infra(self) -> None:
        """A job named 'test-docker' has 'test' which is verify, takes priority."""
        assert classify_job(_job("test-docker", stage="test")) is None

    def test_unknown_job_runs(self) -> None:
        """Jobs matching neither verify nor infra get benefit of the doubt."""
        assert classify_job(_job("custom-stuff", stage="misc")) is None

    def test_stage_based_detection(self) -> None:
        """A job in stage 'deploy' is infra even if name is neutral."""
        reason = classify_job(_job("webhook", stage="deploy"))
        assert reason is not None
        assert "infrastructure" in reason

    def test_verify_stage_allows_run(self) -> None:
        """A job in stage 'check' runs even if name is neutral."""
        assert classify_job(_job("custom-check-thing", stage="check")) is None


# ---------------------------------------------------------------------------
# apply_filter with auto_classify
# ---------------------------------------------------------------------------


def test_auto_classify_filters_infra_jobs() -> None:
    jobs = [
        _job("lint", stage="check"),
        _job("test:unit", stage="check"),
        _job("audit", stage="check"),
        _job("build", stage="build"),
        _job("docker:build", stage="docker"),
        _job("deploy:dokploy", stage="deploy"),
        _job("cleanup:images", stage="cleanup"),
    ]
    filtered = apply_filter(jobs, FilterConfig())
    names_running = [j.name for j in filtered if j.skip_reason is None]
    names_skipped = [j.name for j in filtered if j.skip_reason is not None]
    assert names_running == ["lint", "test:unit", "audit", "build"]
    assert "docker:build" in names_skipped
    assert "deploy:dokploy" in names_skipped
    assert "cleanup:images" in names_skipped


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


def test_only_overrides_classification() -> None:
    """When --jobs is set, classification is bypassed."""
    jobs = [_job("docker:build", stage="docker")]
    cfg = FilterConfig(only=["docker"])
    filtered = apply_filter(jobs, cfg)
    assert filtered[0].skip_reason is None


def test_auto_classify_disabled() -> None:
    """When auto_classify=False, all jobs run."""
    jobs = [
        _job("lint", stage="check"),
        _job("docker:build", stage="docker"),
    ]
    cfg = FilterConfig(auto_classify=False)
    filtered = apply_filter(jobs, cfg)
    assert all(j.skip_reason is None for j in filtered)
