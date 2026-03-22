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
