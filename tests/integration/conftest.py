"""Shared fixtures for integration tests."""
from __future__ import annotations

import pytest

from stitch_agent.adapters.github import GitHubAdapter
from stitch_agent.adapters.gitlab import GitLabAdapter
from stitch_agent.models import FixRequest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GL_HOST = "https://gitlab.example.com"
GL_BASE = f"{GL_HOST}/api/v4"
GL_PROJECT_ID = "42"

GH_BASE = "https://api.github.com"
GH_PROJECT_ID = "owner/repo"
GH_OWNER = "owner"
GH_REPO = "repo"

ANSI_LOG = (
    "\x1b[32m✓\x1b[0m test passed\n"
    "\x1b[31m✗\x1b[0m \x1b[1mtest_foo.py:10\x1b[0m: AssertionError: assert 1 == 2\n"
    "\x1b[1;31mERROR\x1b[0m tests/test_foo.py - assert 1 == 2\n"
)

REALISTIC_LOG = (
    "Running pytest ...\n"
    "FAILED tests/test_math.py::test_add - AssertionError: assert 3 == 4\n"
    "short test summary info\n"
    "FAILED tests/test_math.py::test_add\n"
    "1 failed in 0.12s\n"
)


# ---------------------------------------------------------------------------
# GitLab fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def gl_adapter():
    a = GitLabAdapter(token="gl-test-token", base_url=GL_HOST)
    yield a
    await a.aclose()


@pytest.fixture
def gl_request() -> FixRequest:
    return FixRequest(
        platform="gitlab",
        project_id=GL_PROJECT_ID,
        pipeline_id="100",
        job_id="200",
        branch="main",
    )


@pytest.fixture
def gl_fix_request() -> FixRequest:
    """FixRequest on a stitch/fix-* branch (for verify/retry scenarios)."""
    return FixRequest(
        platform="gitlab",
        project_id=GL_PROJECT_ID,
        pipeline_id="200",
        job_id="300",
        branch="stitch/fix-100",
    )


# ---------------------------------------------------------------------------
# GitHub fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def gh_adapter():
    a = GitHubAdapter(token="gh-test-token")
    yield a
    await a.aclose()


@pytest.fixture
def gh_request() -> FixRequest:
    return FixRequest(
        platform="github",
        project_id=GH_PROJECT_ID,
        pipeline_id="999",
        job_id="12345",
        branch="main",
    )


@pytest.fixture
def gh_fix_request() -> FixRequest:
    return FixRequest(
        platform="github",
        project_id=GH_PROJECT_ID,
        pipeline_id="1000",
        job_id="99999",
        branch="stitch/fix-999",
    )


# ---------------------------------------------------------------------------
# Helper response builders
# ---------------------------------------------------------------------------


def make_gl_mr_list(iids: list[int]) -> list[dict]:
    return [{"iid": iid, "web_url": f"https://gitlab.example.com/p/mr/{iid}"} for iid in iids]


def make_gl_diff_items(paths: list[str]) -> list[dict]:
    return [
        {
            "old_path": p,
            "new_path": p,
            "diff": f"@@ -1 +1 @@\n-old_{p}\n+new_{p}\n",
        }
        for p in paths
    ]


def make_gh_pr_files(paths: list[str]) -> list[dict]:
    return [
        {
            "filename": p,
            "patch": f"@@ -1 +1 @@\n-old_{p}\n+new_{p}\n",
        }
        for p in paths
    ]
