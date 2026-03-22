from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from stitch_agent.adapters.github import GitHubAdapter
from stitch_agent.models import FixRequest

pytestmark = pytest.mark.asyncio

BASE = "https://api.github.com"
PROJECT_ID = "acme/myrepo"
OWNER = "acme"
REPO = "myrepo"


@pytest.fixture
def req() -> FixRequest:
    return FixRequest(
        platform="github",
        project_id=PROJECT_ID,
        pipeline_id="999",
        job_id="12345",
        branch="feature/cool",
    )


@pytest.fixture
async def adapter() -> AsyncGenerator[GitHubAdapter, None]:
    a = GitHubAdapter(token="gh-test-token")
    yield a
    await a.aclose()


@respx.mock
async def test_fetch_job_logs(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/actions/jobs/12345/logs").mock(
        return_value=httpx.Response(200, text="::error::some error\ntest failed\n")
    )
    log = await adapter.fetch_job_logs(req)
    assert "error" in log


@respx.mock
async def test_fetch_job_logs_via_run_fallback(adapter: GitHubAdapter) -> None:
    req = FixRequest(
        platform="github",
        project_id=PROJECT_ID,
        pipeline_id="999",
        job_id="not-numeric",
        branch="main",
    )
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/actions/runs/999/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 42, "conclusion": "failure"}]})
    )
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/actions/jobs/42/logs").mock(
        return_value=httpx.Response(200, text="failed!\n")
    )
    log = await adapter.fetch_job_logs(req)
    assert "failed" in log


@respx.mock
async def test_fetch_diff_via_pr(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/pulls").mock(
        return_value=httpx.Response(200, json=[{"number": 3}])
    )
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/pulls/3/files").mock(
        return_value=httpx.Response(
            200, json=[{"filename": "src/app.py", "patch": "@@ -1 +1 @@\n-old\n+new\n"}]
        )
    )
    diff = await adapter.fetch_diff(req)
    assert "src/app.py" in diff


@respx.mock
async def test_fetch_file_content_raw(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/contents/src%2Fapp.py").mock(
        return_value=httpx.Response(200, text="x = 1\n", headers={"content-type": "text/plain"})
    )
    content = await adapter.fetch_file_content(req, "src/app.py")
    assert content == "x = 1\n"


@respx.mock
async def test_create_fix_branch_git_trees_api(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/git/ref/heads/feature/cool").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "base-sha"}})
    )
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/git/commits/base-sha").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "base-tree-sha"}})
    )
    respx.post(f"{BASE}/repos/{OWNER}/{REPO}/git/trees").mock(
        return_value=httpx.Response(201, json={"sha": "new-tree-sha"})
    )
    respx.post(f"{BASE}/repos/{OWNER}/{REPO}/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": "new-commit-sha"})
    )
    respx.post(f"{BASE}/repos/{OWNER}/{REPO}/git/refs").mock(
        return_value=httpx.Response(201, json={"ref": "refs/heads/stitch/fix-999"})
    )
    branch = await adapter.create_fix_branch(
        request=req,
        fix_id="999",
        changes=[{"path": "src/app.py", "content": "fixed\n"}],
        commit_message="fix(lint): clean up",
    )
    assert branch == "stitch/fix-999"


@respx.mock
async def test_create_merge_request_new(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/pulls").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{BASE}/repos/{OWNER}/{REPO}/pulls").mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/myrepo/pull/5"})
    )
    url = await adapter.create_merge_request(
        request=req,
        fix_branch="stitch/fix-999",
        title="stitch: fix lint",
        description="Auto fix",
    )
    assert url == "https://github.com/acme/myrepo/pull/5"


@respx.mock
async def test_create_merge_request_dedup(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/pulls").mock(
        return_value=httpx.Response(
            200, json=[{"html_url": "https://github.com/acme/myrepo/pull/3"}]
        )
    )
    url = await adapter.create_merge_request(
        request=req,
        fix_branch="stitch/fix-999",
        title="stitch: fix lint",
        description="Auto fix",
    )
    assert url == "https://github.com/acme/myrepo/pull/3"


@respx.mock
async def test_get_repo_config_found(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/contents/.stitch.yml").mock(
        return_value=httpx.Response(
            200, text="languages: [python]\n", headers={"content-type": "text/plain"}
        )
    )
    raw = await adapter.get_repo_config(req)
    assert raw == "languages: [python]\n"


@respx.mock
async def test_get_repo_config_not_found(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/contents/.stitch.yml").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    raw = await adapter.get_repo_config(req)
    assert raw is None


@respx.mock
async def test_get_previous_fix_count(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"head": {"ref": "stitch/fix-100"}},
                {"head": {"ref": "stitch/fix-200"}},
                {"head": {"ref": "feat/unrelated"}},
            ],
        )
    )
    count = await adapter.get_previous_fix_count(req)
    assert count == 2


@respx.mock
async def test_list_failed_jobs(adapter: GitHubAdapter) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/actions/runs/999/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"id": 101, "name": "build", "conclusion": "success"},
                    {"id": 102, "name": "lint", "conclusion": "failure"},
                    {"id": 103, "name": "test", "conclusion": "failure"},
                ]
            },
        )
    )
    jobs = await adapter.list_failed_jobs(PROJECT_ID, "999")
    assert len(jobs) == 2
    assert jobs[0]["id"] == "102"
    assert jobs[0]["name"] == "lint"
    assert jobs[1]["id"] == "103"


@respx.mock
async def test_list_failed_jobs_empty(adapter: GitHubAdapter) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}/actions/runs/999/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": []})
    )
    jobs = await adapter.list_failed_jobs(PROJECT_ID, "999")
    assert jobs == []


@respx.mock
async def test_get_clone_url(adapter: GitHubAdapter, req: FixRequest) -> None:
    respx.get(f"{BASE}/repos/{OWNER}/{REPO}").mock(
        return_value=httpx.Response(200, json={"clone_url": "https://github.com/acme/myrepo.git"})
    )
    url = await adapter.get_clone_url(req)
    assert url == "https://x-access-token:gh-test-token@github.com/acme/myrepo.git"
