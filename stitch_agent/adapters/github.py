from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from stitch_agent.adapters.base import CIPlatformAdapter

if TYPE_CHECKING:
    from stitch_agent.models import FixRequest

_RAW = "application/vnd.github.raw"
_JSON = "application/vnd.github+json"


class GitHubAdapter(CIPlatformAdapter):
    DEFAULT_BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": _JSON,
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def _owner_repo(self, project_id: str) -> tuple[str, str]:
        owner, repo = project_id.split("/", 1)
        return owner, repo

    def _repo(self, project_id: str) -> str:
        owner, repo = self._owner_repo(project_id)
        return f"/repos/{owner}/{repo}"

    async def fetch_job_logs(self, request: FixRequest) -> str:
        owner, repo = self._owner_repo(request.project_id)
        run_id = request.pipeline_id
        job_id = request.job_id

        if not job_id.lstrip("-").isdigit():
            job_id = await self._first_failed_job_id(run_id, request.project_id)

        resp = await self._client.get(
            f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
            headers={"Accept": "text/plain"},
        )
        if resp.status_code == 302:
            redirect_resp = await self._client.get(resp.headers["Location"])
            return redirect_resp.text
        resp.raise_for_status()
        return resp.text

    async def _first_failed_job_id(self, run_id: str, project_id: str) -> str:
        owner, repo = self._owner_repo(project_id)
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
            params={"filter": "latest"},
        )
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        for job in jobs:
            if job.get("conclusion") == "failure":
                return str(job["id"])
        return str(jobs[0]["id"]) if jobs else run_id

    async def fetch_diff(self, request: FixRequest) -> str:
        owner, repo = self._owner_repo(request.project_id)
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "head": f"{owner}:{request.branch}", "per_page": 1},
        )
        resp.raise_for_status()
        prs = resp.json()
        if prs:
            pr_number = prs[0]["number"]
            fr = await self._client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}/files")
            fr.raise_for_status()
            return _format_pr_files(fr.json())
        return await self._commit_diff(request)

    async def _commit_diff(self, request: FixRequest) -> str:
        owner, repo = self._owner_repo(request.project_id)
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/commits",
            params={"sha": request.branch, "per_page": 1},
        )
        resp.raise_for_status()
        commits = resp.json()
        if not commits:
            return "(no commits found)"
        sha = commits[0]["sha"]
        dr = await self._client.get(f"/repos/{owner}/{repo}/commits/{sha}")
        dr.raise_for_status()
        files = dr.json().get("files", [])
        return _format_pr_files(files)

    async def fetch_file_content(self, request: FixRequest, file_path: str) -> str:
        owner, repo = self._owner_repo(request.project_id)
        encoded = quote(file_path, safe="")
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/contents/{encoded}",
            params={"ref": request.branch},
            headers={"Accept": _RAW},
        )
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
            if "content" in data:
                return base64.b64decode(data["content"]).decode()
        return resp.text

    async def create_fix_branch(
        self,
        request: FixRequest,
        fix_id: str,
        changes: list[dict[str, str]],
        commit_message: str,
    ) -> str:
        owner, repo = self._owner_repo(request.project_id)
        fix_branch = f"stitch/fix-{fix_id}"

        ref_resp = await self._client.get(f"/repos/{owner}/{repo}/git/ref/heads/{request.branch}")
        ref_resp.raise_for_status()
        base_sha = ref_resp.json()["object"]["sha"]

        commit_resp = await self._client.get(f"/repos/{owner}/{repo}/git/commits/{base_sha}")
        commit_resp.raise_for_status()
        base_tree_sha = commit_resp.json()["tree"]["sha"]

        tree_items = [
            {"path": c["path"], "mode": "100644", "type": "blob", "content": c["content"]}
            for c in changes
        ]
        tree_resp = await self._client.post(
            f"/repos/{owner}/{repo}/git/trees",
            json={"base_tree": base_tree_sha, "tree": tree_items},
        )
        tree_resp.raise_for_status()
        new_tree_sha = tree_resp.json()["sha"]

        new_commit_resp = await self._client.post(
            f"/repos/{owner}/{repo}/git/commits",
            json={
                "message": commit_message,
                "tree": new_tree_sha,
                "parents": [base_sha],
            },
        )
        new_commit_resp.raise_for_status()
        new_commit_sha = new_commit_resp.json()["sha"]

        create_ref_resp = await self._client.post(
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{fix_branch}", "sha": new_commit_sha},
        )
        create_ref_resp.raise_for_status()
        return fix_branch

    async def create_merge_request(
        self,
        request: FixRequest,
        fix_branch: str,
        title: str,
        description: str,
    ) -> str:
        owner, repo = self._owner_repo(request.project_id)

        existing = await self._client.get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "head": f"{owner}:{fix_branch}", "per_page": 1},
        )
        existing.raise_for_status()
        if existing.json():
            return existing.json()[0]["html_url"]

        resp = await self._client.post(
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": description,
                "head": fix_branch,
                "base": request.branch,
            },
        )
        resp.raise_for_status()
        return resp.json()["html_url"]

    async def get_repo_config(self, request: FixRequest) -> str | None:
        try:
            return await self.fetch_file_content(request, ".stitch.yml")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_previous_fix_count(self, request: FixRequest) -> int:
        owner, repo = self._owner_repo(request.project_id)
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "all", "per_page": 100},
        )
        resp.raise_for_status()
        return sum(
            1 for pr in resp.json() if pr.get("head", {}).get("ref", "").startswith("stitch/fix-")
        )

    async def list_failed_jobs(
        self, project_id: str, pipeline_id: str
    ) -> list[dict[str, str | int]]:
        owner, repo = self._owner_repo(project_id)
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/actions/runs/{pipeline_id}/jobs",
            params={"filter": "latest"},
        )
        resp.raise_for_status()
        return [
            {"id": str(j["id"]), "name": j.get("name", ""), "status": j.get("conclusion", "")}
            for j in resp.json().get("jobs", [])
            if j.get("conclusion") == "failure"
        ]

    async def get_clone_url(self, request: FixRequest) -> str:
        owner, repo = self._owner_repo(request.project_id)
        resp = await self._client.get(f"/repos/{owner}/{repo}")
        resp.raise_for_status()
        clone_url: str = resp.json()["clone_url"]
        scheme, rest = clone_url.split("://", 1)
        return f"{scheme}://x-access-token:{self.token}@{rest}"

    async def get_latest_commit_message(self, project_id: str, branch: str) -> str:
        owner, repo = self._owner_repo(project_id)
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/commits",
            params={"sha": branch, "per_page": 1},
        )
        resp.raise_for_status()
        commits = resp.json()
        if not commits:
            return ""
        return commits[0].get("commit", {}).get("message", "")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def _format_pr_files(files: list[dict[str, str]]) -> str:
    parts = []
    for f in files:
        patch = f.get("patch", "")
        path = f.get("filename", f.get("new_path", ""))
        parts.append(f"--- a/{path}\n+++ b/{path}\n{patch}")
    return "\n".join(parts)
