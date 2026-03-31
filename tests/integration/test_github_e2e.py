"""End-to-end GitHub adapter integration tests with mocked HTTP."""
from __future__ import annotations

import base64

import httpx
import pytest
import respx

from stitch_agent.adapters.github import GitHubAdapter
from stitch_agent.models import FixRequest
from tests.integration.conftest import (
    GH_BASE,
    GH_OWNER,
    GH_PROJECT_ID,
    GH_REPO,
    REALISTIC_LOG,
    make_gh_pr_files,
)

pytestmark = pytest.mark.asyncio

_REPO_PATH = f"/repos/{GH_OWNER}/{GH_REPO}"


# ---------------------------------------------------------------------------
# Full fix flow
# ---------------------------------------------------------------------------


@respx.mock
async def test_full_fix_flow(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """fetch_job_logs (302 redirect) → fetch_file_content → create_fix_branch → create_merge_request."""
    # 1. fetch_job_logs with 302 redirect
    log_url = "https://objects.githubusercontent.com/logs/12345"
    respx.get(f"{GH_BASE}{_REPO_PATH}/actions/jobs/12345/logs").mock(
        return_value=httpx.Response(302, headers={"Location": log_url})
    )
    respx.get(log_url).mock(
        return_value=httpx.Response(200, text=REALISTIC_LOG)
    )
    logs = await gh_adapter.fetch_job_logs(gh_request)
    assert "FAILED" in logs

    # 2. fetch_file_content
    respx.get(f"{GH_BASE}{_REPO_PATH}/contents/src%2Fmath.py").mock(
        return_value=httpx.Response(
            200, text="def add(a, b):\n    return a + b\n",
            headers={"content-type": "text/plain"},
        )
    )
    content = await gh_adapter.fetch_file_content(gh_request, "src/math.py")
    assert "def add" in content

    # 3. create_fix_branch (multi-step)
    base_sha = "aabbccdd"
    tree_sha = "ttreeshaa"
    new_tree_sha = "nnewtreee"
    new_commit_sha = "nnewcmmit"
    respx.get(f"{GH_BASE}{_REPO_PATH}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": base_sha}})
    )
    respx.get(f"{GH_BASE}{_REPO_PATH}/git/commits/{base_sha}").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": tree_sha}})
    )
    respx.post(f"{GH_BASE}{_REPO_PATH}/git/trees").mock(
        return_value=httpx.Response(201, json={"sha": new_tree_sha})
    )
    respx.post(f"{GH_BASE}{_REPO_PATH}/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": new_commit_sha})
    )
    respx.post(f"{GH_BASE}{_REPO_PATH}/git/refs").mock(
        return_value=httpx.Response(201, json={"ref": "refs/heads/stitch/fix-999"})
    )
    fix_branch = await gh_adapter.create_fix_branch(
        request=gh_request,
        fix_id="999",
        changes=[{"path": "src/math.py", "content": "def add(a, b):\n    return a + b + 0\n"}],
        commit_message="fix(test): correct add\n\nStitch-Target: main",
    )
    assert fix_branch == "stitch/fix-999"

    # 4. create_merge_request (no existing PR)
    respx.get(f"{GH_BASE}{_REPO_PATH}/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(f"{GH_BASE}{_REPO_PATH}/pulls").mock(
        return_value=httpx.Response(201, json={"html_url": f"https://github.com/{GH_PROJECT_ID}/pull/1"})
    )
    mr_url = await gh_adapter.create_merge_request(
        request=gh_request,
        fix_branch=fix_branch,
        title="stitch: fix test",
        description="Auto fix",
    )
    assert mr_url == f"https://github.com/{GH_PROJECT_ID}/pull/1"


# ---------------------------------------------------------------------------
# Non-numeric job_id fallback
# ---------------------------------------------------------------------------


@respx.mock
async def test_non_numeric_job_id_fallback(gh_adapter: GitHubAdapter) -> None:
    """Falls back to _first_failed_job_id when job_id isn't numeric."""
    request = FixRequest(
        platform="github",
        project_id=GH_PROJECT_ID,
        pipeline_id="999",
        job_id="not-a-number",
        branch="main",
    )
    # First call: list jobs to find the first failed one
    respx.get(f"{GH_BASE}{_REPO_PATH}/actions/runs/999/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"id": 55, "conclusion": "success"},
                    {"id": 66, "conclusion": "failure"},
                    {"id": 77, "conclusion": "failure"},
                ]
            },
        )
    )
    # Second call: fetch logs for the first failed job (id=66)
    respx.get(f"{GH_BASE}{_REPO_PATH}/actions/jobs/66/logs").mock(
        return_value=httpx.Response(200, text="job 66 failed\n")
    )
    logs = await gh_adapter.fetch_job_logs(request)
    assert "job 66 failed" in logs


# ---------------------------------------------------------------------------
# Base64 file content
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_file_content_base64(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """Handles JSON response with base64-encoded content field."""
    raw_content = "SECRET_KEY = 'abc123'\nDEBUG = False\n"
    encoded = base64.b64encode(raw_content.encode()).decode()
    respx.get(f"{GH_BASE}{_REPO_PATH}/contents/config%2Fsettings.py").mock(
        return_value=httpx.Response(
            200,
            json={"content": encoded, "encoding": "base64", "name": "settings.py"},
            headers={"content-type": "application/json"},
        )
    )
    content = await gh_adapter.fetch_file_content(gh_request, "config/settings.py")
    assert content == raw_content
    assert "SECRET_KEY" in content


# ---------------------------------------------------------------------------
# PR deduplication
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_merge_request_dedup(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """When existing PR found, returns its URL instead of creating new."""
    existing_url = f"https://github.com/{GH_PROJECT_ID}/pull/42"
    respx.get(f"{GH_BASE}{_REPO_PATH}/pulls").mock(
        return_value=httpx.Response(200, json=[{"html_url": existing_url}])
    )
    url = await gh_adapter.create_merge_request(
        request=gh_request,
        fix_branch="stitch/fix-999",
        title="stitch: fix lint",
        description="Auto fix",
    )
    assert url == existing_url
    # POST should NOT have been called — only the GET for existing PRs
    assert not any(r.request.method == "POST" for r in respx.calls)


# ---------------------------------------------------------------------------
# Diff via PR path
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_diff_via_pr(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """Gets file diffs from pull request API when a PR exists."""
    respx.get(f"{GH_BASE}{_REPO_PATH}/pulls").mock(
        return_value=httpx.Response(200, json=[{"number": 3}])
    )
    respx.get(f"{GH_BASE}{_REPO_PATH}/pulls/3/files").mock(
        return_value=httpx.Response(200, json=make_gh_pr_files(["src/app.py", "tests/test_app.py"]))
    )
    diff = await gh_adapter.fetch_diff(gh_request)
    assert "src/app.py" in diff
    assert "tests/test_app.py" in diff


# ---------------------------------------------------------------------------
# Diff via commit fallback
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_diff_via_commit_fallback(
    gh_adapter: GitHubAdapter, gh_request: FixRequest
) -> None:
    """Falls back to commit API when no PR exists."""
    respx.get(f"{GH_BASE}{_REPO_PATH}/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{GH_BASE}{_REPO_PATH}/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": "cafebabe"}])
    )
    respx.get(f"{GH_BASE}{_REPO_PATH}/commits/cafebabe").mock(
        return_value=httpx.Response(
            200,
            json={"files": make_gh_pr_files(["README.md"])},
        )
    )
    diff = await gh_adapter.fetch_diff(gh_request)
    assert "README.md" in diff


# ---------------------------------------------------------------------------
# Self-hosted base URL (GitHub Enterprise)
# ---------------------------------------------------------------------------


@respx.mock
async def test_self_hosted_base_url() -> None:
    """Adapter works with a GitHub Enterprise URL."""
    ghe_base = "https://github.mycompany.com/api/v3"
    adapter = GitHubAdapter(token="ghe-token", base_url=ghe_base)
    try:
        respx.get(f"{ghe_base}/repos/{GH_OWNER}/{GH_REPO}/actions/jobs/42/logs").mock(
            return_value=httpx.Response(200, text="enterprise log\n")
        )
        request = FixRequest(
            platform="github",
            project_id=GH_PROJECT_ID,
            pipeline_id="1",
            job_id="42",
            branch="main",
        )
        result = await adapter.fetch_job_logs(request)
        assert "enterprise log" in result
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# Repo config not found
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_repo_config_not_found(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """Returns None on 404 for .stitch.yml."""
    respx.get(f"{GH_BASE}{_REPO_PATH}/contents/.stitch.yml").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    result = await gh_adapter.get_repo_config(gh_request)
    assert result is None


@respx.mock
async def test_get_repo_config_found(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """Returns config content when .stitch.yml exists."""
    yaml_content = "languages: [python]\ntest_runner: pytest\n"
    respx.get(f"{GH_BASE}{_REPO_PATH}/contents/.stitch.yml").mock(
        return_value=httpx.Response(
            200, text=yaml_content, headers={"content-type": "text/plain"}
        )
    )
    result = await gh_adapter.get_repo_config(gh_request)
    assert result == yaml_content


# ---------------------------------------------------------------------------
# Error on 403
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_job_logs_403_raises(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """Permission denied raises HTTPStatusError."""
    respx.get(f"{GH_BASE}{_REPO_PATH}/actions/jobs/12345/logs").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible by integration"})
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await gh_adapter.fetch_job_logs(gh_request)
    assert exc_info.value.response.status_code == 403


# ---------------------------------------------------------------------------
# Search codebase
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_codebase(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """Returns results from search API."""
    respx.get(f"{GH_BASE}/search/code").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 2,
                "items": [
                    {"path": "src/utils.py", "name": "utils.py"},
                    {"path": "src/helpers.py", "name": "helpers.py"},
                ],
            },
        )
    )
    results = await gh_adapter.search_codebase(gh_request, "def helper", max_results=10)
    assert len(results) == 2
    assert results[0]["path"] == "src/utils.py"
    assert results[1]["path"] == "src/helpers.py"


@respx.mock
async def test_search_codebase_empty(gh_adapter: GitHubAdapter, gh_request: FixRequest) -> None:
    """Returns empty list when search finds nothing."""
    respx.get(f"{GH_BASE}/search/code").mock(
        return_value=httpx.Response(200, json={"total_count": 0, "items": []})
    )
    results = await gh_adapter.search_codebase(gh_request, "nonexistent_function_xyz")
    assert results == []
