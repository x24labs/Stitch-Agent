from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from stitch_agent.adapters.gitlab import GitLabAdapter
from stitch_agent.models import FixRequest

pytestmark = pytest.mark.asyncio

BASE = "https://gitlab.com/api/v4"
PROJECT_ID = "42"
ENCODED_PID = "42"


@pytest.fixture
def request_() -> FixRequest:
    return FixRequest(
        platform="gitlab",
        project_id=PROJECT_ID,
        pipeline_id="100",
        job_id="200",
        branch="feature/my-fix",
    )


@pytest.fixture
async def adapter() -> AsyncGenerator[GitLabAdapter, None]:
    a = GitLabAdapter(token="test-token", base_url="https://gitlab.com")
    yield a
    await a.aclose()


@respx.mock
async def test_fetch_job_logs(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.get(f"{BASE}/projects/{ENCODED_PID}/jobs/200/trace").mock(
        return_value=httpx.Response(200, text="Running job...\nError: F401\n")
    )
    log = await adapter.fetch_job_logs(request_)
    assert "F401" in log


@respx.mock
async def test_fetch_diff_via_mr(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.get(f"{BASE}/projects/{ENCODED_PID}/merge_requests").mock(
        return_value=httpx.Response(200, json=[{"iid": 7}])
    )
    respx.get(f"{BASE}/projects/{ENCODED_PID}/merge_requests/7/diffs").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "old_path": "src/foo.py",
                    "new_path": "src/foo.py",
                    "diff": "@@ -1 +1 @@\n-old\n+new\n",
                }
            ],
        )
    )
    diff = await adapter.fetch_diff(request_)
    assert "src/foo.py" in diff


@respx.mock
async def test_fetch_diff_fallback_to_commit(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.get(f"{BASE}/projects/{ENCODED_PID}/merge_requests").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/projects/{ENCODED_PID}/repository/commits").mock(
        return_value=httpx.Response(200, json=[{"id": "abc123"}])
    )
    respx.get(f"{BASE}/projects/{ENCODED_PID}/repository/commits/abc123/diff").mock(
        return_value=httpx.Response(
            200, json=[{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1 +1 @@\n"}]
        )
    )
    diff = await adapter.fetch_diff(request_)
    assert "a.py" in diff


@respx.mock
async def test_fetch_file_content(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.get(f"{BASE}/projects/{ENCODED_PID}/repository/files/src%2Fmain.py/raw").mock(
        return_value=httpx.Response(200, text="x = 1\n")
    )
    content = await adapter.fetch_file_content(request_, "src/main.py")
    assert content == "x = 1\n"


@respx.mock
async def test_create_fix_branch(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.post(f"{BASE}/projects/{ENCODED_PID}/repository/commits").mock(
        return_value=httpx.Response(
            201, json={"id": "new-sha", "web_url": "https://gitlab.com/p/c/new-sha"}
        )
    )
    branch = await adapter.create_fix_branch(
        request=request_,
        fix_id="100",
        changes=[{"path": "src/main.py", "content": "fixed\n"}],
        commit_message="fix(lint): remove unused import",
    )
    assert branch == "stitch/fix-100"


@respx.mock
async def test_create_merge_request(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.post(f"{BASE}/projects/{ENCODED_PID}/merge_requests").mock(
        return_value=httpx.Response(201, json={"web_url": "https://gitlab.com/p/mr/5"})
    )
    url = await adapter.create_merge_request(
        request=request_,
        fix_branch="stitch/fix-100",
        title="stitch: fix lint",
        description="Automated fix",
    )
    assert url == "https://gitlab.com/p/mr/5"


@respx.mock
async def test_get_repo_config_found(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.get(f"{BASE}/projects/{ENCODED_PID}/repository/files/.stitch.yml/raw").mock(
        return_value=httpx.Response(200, text="languages: [python]\n")
    )
    raw = await adapter.get_repo_config(request_)
    assert raw == "languages: [python]\n"


@respx.mock
async def test_get_repo_config_not_found(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.get(f"{BASE}/projects/{ENCODED_PID}/repository/files/.stitch.yml/raw").mock(
        return_value=httpx.Response(404, json={"message": "404 File Not Found"})
    )
    raw = await adapter.get_repo_config(request_)
    assert raw is None


@respx.mock
async def test_get_previous_fix_count(adapter: GitLabAdapter, request_: FixRequest) -> None:
    respx.get(f"{BASE}/projects/{ENCODED_PID}/merge_requests").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"source_branch": "stitch/fix-1"},
                {"source_branch": "stitch/fix-2"},
                {"source_branch": "other-branch"},
            ],
        )
    )
    count = await adapter.get_previous_fix_count(request_)
    assert count == 2
