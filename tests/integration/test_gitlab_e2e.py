"""End-to-end GitLab adapter integration tests with mocked HTTP."""
from __future__ import annotations

import httpx
import pytest
import respx

from stitch_agent.adapters.gitlab import GitLabAdapter
from stitch_agent.models import FixRequest
from tests.integration.conftest import (
    ANSI_LOG,
    GL_BASE,
    GL_HOST,
    GL_PROJECT_ID,
    REALISTIC_LOG,
    make_gl_diff_items,
    make_gl_mr_list,
)

pytestmark = pytest.mark.asyncio

_PID = GL_PROJECT_ID
_PROJ = f"/projects/{_PID}"


# ---------------------------------------------------------------------------
# Full fix flow
# ---------------------------------------------------------------------------


@respx.mock
async def test_full_fix_flow(gl_adapter: GitLabAdapter, gl_request: FixRequest) -> None:
    """fetch_job_logs → fetch_file_content → create_fix_branch → create_merge_request."""
    # 1. fetch_job_logs
    respx.get(f"{GL_BASE}{_PROJ}/jobs/200/trace").mock(
        return_value=httpx.Response(200, text=REALISTIC_LOG)
    )
    logs = await gl_adapter.fetch_job_logs(gl_request)
    assert "FAILED" in logs

    # 2. fetch_file_content
    respx.get(f"{GL_BASE}{_PROJ}/repository/files/src%2Fmath.py/raw").mock(
        return_value=httpx.Response(200, text="def add(a, b):\n    return a + b\n")
    )
    content = await gl_adapter.fetch_file_content(gl_request, "src/math.py")
    assert "def add" in content

    # 3. create_fix_branch
    respx.post(f"{GL_BASE}{_PROJ}/repository/commits").mock(
        return_value=httpx.Response(201, json={"id": "abc123", "web_url": f"{GL_HOST}/p/c/abc123"})
    )
    fix_branch = await gl_adapter.create_fix_branch(
        request=gl_request,
        fix_id="100",
        changes=[{"path": "src/math.py", "content": "def add(a, b):\n    return a + b + 0\n"}],
        commit_message="fix(test): correct add return value\n\nStitch-Target: main",
    )
    assert fix_branch == "stitch/fix-100"

    # 4. create_merge_request
    respx.post(f"{GL_BASE}{_PROJ}/merge_requests").mock(
        return_value=httpx.Response(201, json={"web_url": f"{GL_HOST}/p/mr/1"})
    )
    mr_url = await gl_adapter.create_merge_request(
        request=gl_request,
        fix_branch=fix_branch,
        title="stitch: fix test",
        description="Auto fix",
    )
    assert mr_url == f"{GL_HOST}/p/mr/1"


# ---------------------------------------------------------------------------
# ANSI code stripping
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_job_logs_strips_ansi(gl_adapter: GitLabAdapter, gl_request: FixRequest) -> None:
    """Job logs with real ANSI escape sequences are cleaned before being returned."""
    respx.get(f"{GL_BASE}{_PROJ}/jobs/200/trace").mock(
        return_value=httpx.Response(200, text=ANSI_LOG)
    )
    result = await gl_adapter.fetch_job_logs(gl_request)
    assert "\x1b[" not in result
    assert "test passed" in result
    assert "AssertionError" in result


# ---------------------------------------------------------------------------
# File content fallback (400 → tree + blob)
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_file_content_blob_fallback(
    gl_adapter: GitLabAdapter, gl_request: FixRequest
) -> None:
    """When files API returns 400, falls back to tree+blob endpoint."""
    # Primary endpoint returns 400 (self-hosted reverse proxy issue)
    respx.get(f"{GL_BASE}{_PROJ}/repository/files/src%2Fapp.py/raw").mock(
        return_value=httpx.Response(400, text="Bad Request")
    )
    # Tree listing returns blob info
    respx.get(f"{GL_BASE}{_PROJ}/repository/tree").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "app.py", "id": "blobsha123", "type": "blob", "path": "src/app.py"}],
        )
    )
    # Blob raw content
    respx.get(f"{GL_BASE}{_PROJ}/repository/blobs/blobsha123/raw").mock(
        return_value=httpx.Response(200, text="print('hello world')\n")
    )
    content = await gl_adapter.fetch_file_content(gl_request, "src/app.py")
    assert content == "print('hello world')\n"


@respx.mock
async def test_fetch_file_content_blob_fallback_403(
    gl_adapter: GitLabAdapter, gl_request: FixRequest
) -> None:
    """When files API returns 403, also falls back to tree+blob endpoint."""
    respx.get(f"{GL_BASE}{_PROJ}/repository/files/config%2Fsettings.py/raw").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    respx.get(f"{GL_BASE}{_PROJ}/repository/tree").mock(
        return_value=httpx.Response(
            200,
            json=[{"name": "settings.py", "id": "sha456", "type": "blob", "path": "config/settings.py"}],
        )
    )
    respx.get(f"{GL_BASE}{_PROJ}/repository/blobs/sha456/raw").mock(
        return_value=httpx.Response(200, text="DEBUG = True\n")
    )
    content = await gl_adapter.fetch_file_content(gl_request, "config/settings.py")
    assert content == "DEBUG = True\n"


# ---------------------------------------------------------------------------
# Diff via MR path
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_diff_via_mr(gl_adapter: GitLabAdapter, gl_request: FixRequest) -> None:
    """When an open MR exists, gets diffs from MR API."""
    respx.get(f"{GL_BASE}{_PROJ}/merge_requests").mock(
        return_value=httpx.Response(200, json=make_gl_mr_list([7]))
    )
    respx.get(f"{GL_BASE}{_PROJ}/merge_requests/7/diffs").mock(
        return_value=httpx.Response(200, json=make_gl_diff_items(["src/foo.py", "src/bar.py"]))
    )
    diff = await gl_adapter.fetch_diff(gl_request)
    assert "src/foo.py" in diff
    assert "src/bar.py" in diff
    assert "+new_src/foo.py" in diff


# ---------------------------------------------------------------------------
# Diff via commit fallback
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_diff_via_commit_fallback(
    gl_adapter: GitLabAdapter, gl_request: FixRequest
) -> None:
    """When no MR exists, falls back to commit API."""
    respx.get(f"{GL_BASE}{_PROJ}/merge_requests").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{GL_BASE}{_PROJ}/repository/commits").mock(
        return_value=httpx.Response(200, json=[{"id": "deadbeef"}])
    )
    respx.get(f"{GL_BASE}{_PROJ}/repository/commits/deadbeef/diff").mock(
        return_value=httpx.Response(200, json=make_gl_diff_items(["README.md"]))
    )
    diff = await gl_adapter.fetch_diff(gl_request)
    assert "README.md" in diff


# ---------------------------------------------------------------------------
# Self-hosted base URL
# ---------------------------------------------------------------------------


@respx.mock
async def test_self_hosted_base_url() -> None:
    """Adapter works with a custom base_url (not gitlab.com)."""
    custom_host = "https://git.mycompany.internal"
    custom_base = f"{custom_host}/api/v4"
    adapter = GitLabAdapter(token="corp-token", base_url=custom_host)
    try:
        respx.get(f"{custom_base}/projects/99/jobs/10/trace").mock(
            return_value=httpx.Response(200, text="corp log output\n")
        )
        request = FixRequest(
            platform="gitlab",
            project_id="99",
            pipeline_id="50",
            job_id="10",
            branch="develop",
        )
        result = await adapter.fetch_job_logs(request)
        assert "corp log output" in result
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# Repo config not found
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_repo_config_not_found(
    gl_adapter: GitLabAdapter, gl_request: FixRequest
) -> None:
    """Returns None on 404 for .stitch.yml."""
    respx.get(f"{GL_BASE}{_PROJ}/repository/files/.stitch.yml/raw").mock(
        return_value=httpx.Response(404, json={"message": "404 File Not Found"})
    )
    result = await gl_adapter.get_repo_config(gl_request)
    assert result is None


@respx.mock
async def test_get_repo_config_found(
    gl_adapter: GitLabAdapter, gl_request: FixRequest
) -> None:
    """Returns config content when .stitch.yml exists."""
    yaml_content = "languages: [python]\nlinter: ruff\n"
    respx.get(f"{GL_BASE}{_PROJ}/repository/files/.stitch.yml/raw").mock(
        return_value=httpx.Response(200, text=yaml_content)
    )
    result = await gl_adapter.get_repo_config(gl_request)
    assert result == yaml_content


# ---------------------------------------------------------------------------
# Previous fix count
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_previous_fix_count(
    gl_adapter: GitLabAdapter, gl_request: FixRequest
) -> None:
    """Counts stitch/fix-* MRs correctly, ignoring non-stitch branches."""
    respx.get(f"{GL_BASE}{_PROJ}/merge_requests").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"source_branch": "stitch/fix-1"},
                {"source_branch": "stitch/fix-2"},
                {"source_branch": "stitch/fix-3"},
                {"source_branch": "feature/unrelated"},
                {"source_branch": "hotfix/urgent"},
            ],
        )
    )
    count = await gl_adapter.get_previous_fix_count(gl_request)
    assert count == 3


# ---------------------------------------------------------------------------
# CI config validation
# ---------------------------------------------------------------------------


@respx.mock
async def test_validate_ci_config_valid(gl_adapter: GitLabAdapter) -> None:
    """Valid CI config returns (True, '')."""
    respx.post(f"{GL_BASE}{_PROJ}/ci/lint").mock(
        return_value=httpx.Response(200, json={"valid": True, "errors": []})
    )
    valid, msg = await gl_adapter.validate_ci_config(GL_PROJECT_ID, "stages: [test]\n")
    assert valid is True
    assert msg == ""


@respx.mock
async def test_validate_ci_config_invalid(gl_adapter: GitLabAdapter) -> None:
    """Invalid CI config returns (False, error message)."""
    respx.post(f"{GL_BASE}{_PROJ}/ci/lint").mock(
        return_value=httpx.Response(
            200,
            json={
                "valid": False,
                "errors": ["jobs:build config contains unknown keys: badkey"],
            },
        )
    )
    valid, msg = await gl_adapter.validate_ci_config(GL_PROJECT_ID, "bad: yaml\n")
    assert valid is False
    assert "badkey" in msg


@respx.mock
async def test_validate_ci_config_api_error_lets_through(gl_adapter: GitLabAdapter) -> None:
    """If the lint API returns non-200, validation passes (let it through)."""
    respx.post(f"{GL_BASE}{_PROJ}/ci/lint").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    valid, msg = await gl_adapter.validate_ci_config(GL_PROJECT_ID, "stages: [test]\n")
    assert valid is True


# ---------------------------------------------------------------------------
# Authentication error
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_job_logs_401_raises(
    gl_adapter: GitLabAdapter, gl_request: FixRequest
) -> None:
    """Authentication failure raises HTTPStatusError."""
    respx.get(f"{GL_BASE}{_PROJ}/jobs/200/trace").mock(
        return_value=httpx.Response(401, json={"message": "401 Unauthorized"})
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await gl_adapter.fetch_job_logs(gl_request)
    assert exc_info.value.response.status_code == 401


# ---------------------------------------------------------------------------
# List failed jobs with scope encoding
# ---------------------------------------------------------------------------


@respx.mock
async def test_list_failed_jobs_scope_encoding(gl_adapter: GitLabAdapter) -> None:
    """The scope[]=failed query param works and returns only failed jobs."""
    respx.get(f"{GL_BASE}{_PROJ}/pipelines/100/jobs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 201, "name": "lint", "status": "failed"},
                {"id": 202, "name": "test", "status": "failed"},
                {"id": 203, "name": "deploy", "status": "success"},
            ],
        )
    )
    jobs = await gl_adapter.list_failed_jobs(GL_PROJECT_ID, "100")
    assert len(jobs) == 3
    assert all(j["id"] for j in jobs)
    assert jobs[0]["name"] == "lint"
    assert jobs[1]["name"] == "test"


@respx.mock
async def test_list_failed_jobs_empty_pipeline(gl_adapter: GitLabAdapter) -> None:
    """Empty job list returns empty list without error."""
    respx.get(f"{GL_BASE}{_PROJ}/pipelines/100/jobs").mock(
        return_value=httpx.Response(200, json=[])
    )
    jobs = await gl_adapter.list_failed_jobs(GL_PROJECT_ID, "100")
    assert jobs == []
