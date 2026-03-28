from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stitch_agent.models import FixRequest


class CIPlatformAdapter(ABC):
    @abstractmethod
    async def fetch_job_logs(self, request: FixRequest) -> str: ...

    @abstractmethod
    async def fetch_diff(self, request: FixRequest) -> str: ...

    @abstractmethod
    async def fetch_file_content(self, request: FixRequest, file_path: str) -> str: ...

    @abstractmethod
    async def create_fix_branch(
        self,
        request: FixRequest,
        fix_id: str,
        changes: list[dict[str, str]],
        commit_message: str,
    ) -> str: ...

    @abstractmethod
    async def create_merge_request(
        self,
        request: FixRequest,
        fix_branch: str,
        title: str,
        description: str,
    ) -> str: ...

    @abstractmethod
    async def get_repo_config(self, request: FixRequest) -> str | None: ...

    @abstractmethod
    async def get_previous_fix_count(self, request: FixRequest) -> int: ...

    @abstractmethod
    async def get_clone_url(self, request: FixRequest) -> str: ...

    @abstractmethod
    async def list_failed_jobs(
        self, project_id: str, pipeline_id: str
    ) -> list[dict[str, str | int]]: ...

    @abstractmethod
    async def get_latest_commit_message(
        self, project_id: str, branch: str
    ) -> str: ...

    @abstractmethod
    async def push_to_branch(
        self,
        project_id: str,
        branch: str,
        changes: list[dict[str, str]],
        commit_message: str,
    ) -> None: ...

    @abstractmethod
    async def count_branch_commits(
        self, project_id: str, branch: str, target_branch: str
    ) -> int: ...

    @abstractmethod
    async def search_codebase(
        self, request: FixRequest, pattern: str, max_results: int = 20,
    ) -> list[dict[str, str]]: ...

    @abstractmethod
    async def list_directory(
        self, request: FixRequest, path: str = "",
    ) -> list[dict[str, str]]: ...

    async def validate_ci_config(
        self, project_id: str, content: str,
    ) -> tuple[bool, str]:
        """Validate CI config content. Returns (valid, error_message)."""
        return True, ""
