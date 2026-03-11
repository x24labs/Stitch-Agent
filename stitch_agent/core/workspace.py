from __future__ import annotations

import subprocess
import uuid
from pathlib import Path


class WorkspaceManager:
    def __init__(self, workspace_root: str = "/tmp/stitch-workspace") -> None:
        self.root = Path(workspace_root)
        self.repos_dir = self.root / "repos"
        self.fixes_dir = self.root / "fixes"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.fixes_dir.mkdir(parents=True, exist_ok=True)

    def _repo_dir(self, project_id: str) -> Path:
        return self.repos_dir / project_id.replace("/", "-")

    def _run(
        self, args: list[str], cwd: Path | None = None, input: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args, cwd=cwd, input=input, capture_output=True, text=True, check=True
        )

    async def ensure_clone(self, clone_url: str, project_id: str) -> Path:
        repo_dir = self._repo_dir(project_id)
        if repo_dir.exists():
            self._run(["git", "pull", "--ff-only"], cwd=repo_dir)
        else:
            self._run(["git", "clone", clone_url, str(repo_dir)])
        return repo_dir

    async def create_worktree(self, project_id: str, branch: str) -> tuple[Path, str]:
        fix_id = uuid.uuid4().hex[:12]
        repo_dir = self._repo_dir(project_id)
        worktree_path = self.fixes_dir / f"fix-{fix_id}"

        self._run(["git", "fetch", "origin", branch], cwd=repo_dir)
        self._run(
            ["git", "worktree", "add", str(worktree_path), f"origin/{branch}"],
            cwd=repo_dir,
        )
        return worktree_path, fix_id

    async def apply_patch(self, worktree_path: Path, patch: str) -> None:
        self._run(["git", "apply", "--check", "-"], cwd=worktree_path, input=patch)
        self._run(["git", "apply", "-"], cwd=worktree_path, input=patch)

    async def commit_and_push(self, worktree_path: Path, fix_id: str, commit_message: str) -> str:
        fix_branch = f"stitch/fix-{fix_id}"
        self._run(["git", "checkout", "-b", fix_branch], cwd=worktree_path)
        self._run(["git", "add", "-A"], cwd=worktree_path)
        self._run(["git", "commit", "-m", commit_message], cwd=worktree_path)
        self._run(["git", "push", "origin", fix_branch], cwd=worktree_path)
        return fix_branch

    async def cleanup_worktree(self, project_id: str, worktree_path: Path) -> None:
        repo_dir = self._repo_dir(project_id)
        self._run(["git", "worktree", "remove", str(worktree_path), "--force"], cwd=repo_dir)
