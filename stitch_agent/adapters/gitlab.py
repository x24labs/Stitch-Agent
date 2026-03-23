from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from stitch_agent.adapters.base import CIPlatformAdapter

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

if TYPE_CHECKING:
    from stitch_agent.models import FixRequest


class GitLabAdapter(CIPlatformAdapter):
    DEFAULT_BASE_URL = "https://gitlab.com"

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=timeout,
        )

    def _pid(self, project_id: str) -> str:
        return f"/projects/{quote(project_id, safe='')}"

    async def fetch_job_logs(self, request: FixRequest) -> str:
        resp = await self._client.get(
            f"{self._pid(request.project_id)}/jobs/{request.job_id}/trace"
        )
        resp.raise_for_status()
        return _ANSI_RE.sub("", resp.text)

    async def fetch_diff(self, request: FixRequest) -> str:
        resp = await self._client.get(
            f"{self._pid(request.project_id)}/merge_requests",
            params={
                "state": "opened",
                "source_branch": request.branch,
                "order_by": "updated_at",
                "sort": "desc",
                "per_page": 1,
            },
        )
        resp.raise_for_status()
        mrs = resp.json()
        if mrs:
            mr_iid = mrs[0]["iid"]
            dr = await self._client.get(
                f"{self._pid(request.project_id)}/merge_requests/{mr_iid}/diffs"
            )
            dr.raise_for_status()
            return _format_diffs(dr.json())
        return await self._commit_diff(request)

    async def _commit_diff(self, request: FixRequest) -> str:
        resp = await self._client.get(
            f"{self._pid(request.project_id)}/repository/commits",
            params={"ref_name": request.branch, "per_page": 1},
        )
        resp.raise_for_status()
        commits = resp.json()
        if not commits:
            return "(no commits found)"
        sha = commits[0]["id"]
        dr = await self._client.get(
            f"{self._pid(request.project_id)}/repository/commits/{sha}/diff"
        )
        dr.raise_for_status()
        return _format_diffs(dr.json())

    async def fetch_file_content(self, request: FixRequest, file_path: str) -> str:
        # Primary: files API (works on gitlab.com)
        encoded = quote(file_path, safe="")
        resp = await self._client.get(
            f"{self._pid(request.project_id)}/repository/files/{encoded}/raw",
            params={"ref": request.branch},
        )
        if resp.status_code == 200:
            return resp.text

        # 400/403 may be a reverse proxy blocking %2F — try blob fallback
        if resp.status_code in (400, 403):
            return await self._fetch_via_blob(request, file_path)

        # 404 or other errors — raise normally
        resp.raise_for_status()
        return resp.text  # unreachable, satisfies type checker

    async def _fetch_via_blob(self, request: FixRequest, file_path: str) -> str:
        """Fetch file content via tree listing + blob endpoint.

        Works around reverse proxies (Cloudflare, nginx) that reject
        percent-encoded slashes (%2F) in URL paths.
        """
        import posixpath

        parent = posixpath.dirname(file_path)
        name = posixpath.basename(file_path)
        resp = await self._client.get(
            f"{self._pid(request.project_id)}/repository/tree",
            params={"path": parent, "ref": request.branch, "per_page": 100},
        )
        resp.raise_for_status()
        blob_sha: str | None = None
        for item in resp.json():
            if item.get("name") == name and item.get("type") == "blob":
                blob_sha = item["id"]
                break
        if blob_sha is None:
            raise FileNotFoundError(f"File not found in tree: {file_path}")
        blob_resp = await self._client.get(
            f"{self._pid(request.project_id)}/repository/blobs/{blob_sha}/raw"
        )
        blob_resp.raise_for_status()
        return blob_resp.text

    async def create_fix_branch(
        self,
        request: FixRequest,
        fix_id: str,
        changes: list[dict[str, str]],
        commit_message: str,
    ) -> str:
        fix_branch = f"stitch/fix-{fix_id}"
        actions = [
            {"action": "update", "file_path": c["path"], "content": c["content"]} for c in changes
        ]
        resp = await self._client.post(
            f"{self._pid(request.project_id)}/repository/commits",
            json={
                "branch": fix_branch,
                "start_branch": request.branch,
                "commit_message": commit_message,
                "actions": actions,
            },
        )
        resp.raise_for_status()
        return fix_branch

    async def create_merge_request(
        self,
        request: FixRequest,
        fix_branch: str,
        title: str,
        description: str,
    ) -> str:
        resp = await self._client.post(
            f"{self._pid(request.project_id)}/merge_requests",
            json={
                "source_branch": fix_branch,
                "target_branch": request.branch,
                "title": title,
                "description": description,
                "labels": "stitch-agent",
                "remove_source_branch": True,
            },
        )
        resp.raise_for_status()
        return resp.json()["web_url"]

    async def get_repo_config(self, request: FixRequest) -> str | None:
        try:
            return await self.fetch_file_content(request, ".stitch.yml")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_clone_url(self, request: FixRequest) -> str:
        resp = await self._client.get(self._pid(request.project_id))
        resp.raise_for_status()
        data = resp.json()
        http_url: str = data["http_url_to_repo"]
        scheme, rest = http_url.split("://", 1)
        return f"{scheme}://oauth2:{self.token}@{rest}"

    async def get_previous_fix_count(self, request: FixRequest) -> int:
        resp = await self._client.get(
            f"{self._pid(request.project_id)}/merge_requests",
            params={"state": "all", "target_branch": request.branch, "per_page": 100},
        )
        resp.raise_for_status()
        return sum(1 for mr in resp.json() if mr.get("source_branch", "").startswith("stitch/fix-"))

    async def list_failed_jobs(
        self, project_id: str, pipeline_id: str
    ) -> list[dict[str, str | int]]:
        resp = await self._client.get(
            f"{self._pid(project_id)}/pipelines/{pipeline_id}/jobs",
            params={"scope[]": "failed"},
        )
        resp.raise_for_status()
        return [
            {"id": str(j["id"]), "name": j.get("name", ""), "status": j.get("status", "failed")}
            for j in resp.json()
        ]

    async def push_to_branch(
        self,
        project_id: str,
        branch: str,
        changes: list[dict[str, str]],
        commit_message: str,
    ) -> None:
        actions = [
            {"action": "update", "file_path": c["path"], "content": c["content"]}
            for c in changes
        ]
        resp = await self._client.post(
            f"{self._pid(project_id)}/repository/commits",
            json={
                "branch": branch,
                "commit_message": commit_message,
                "actions": actions,
            },
        )
        resp.raise_for_status()

    async def count_branch_commits(
        self, project_id: str, branch: str, target_branch: str
    ) -> int:
        resp = await self._client.get(
            f"{self._pid(project_id)}/repository/compare",
            params={"from": target_branch, "to": branch},
        )
        resp.raise_for_status()
        return len(resp.json().get("commits", []))

    async def get_latest_commit_message(self, project_id: str, branch: str) -> str:
        resp = await self._client.get(
            f"{self._pid(project_id)}/repository/commits",
            params={"ref_name": branch, "per_page": 1},
        )
        resp.raise_for_status()
        commits = resp.json()
        if not commits:
            return ""
        return commits[0].get("message", "")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitLabAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def _format_diffs(diffs: list[dict[str, str]]) -> str:
    parts = []
    for d in diffs:
        parts.append(
            f"--- a/{d.get('old_path', '')}\n+++ b/{d.get('new_path', '')}\n{d.get('diff', '')}"
        )
    return "\n".join(parts)
