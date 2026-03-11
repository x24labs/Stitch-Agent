from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stitch_agent.adapters.base import CIPlatformAdapter
    from stitch_agent.models import ClassificationResult, FixRequest


class PRCreator:
    def __init__(self, adapter: CIPlatformAdapter) -> None:
        self.adapter = adapter

    async def create(
        self,
        request: FixRequest,
        classification: ClassificationResult,
        fix_branch: str,
        explanation: str,
    ) -> str:
        title = f"stitch: fix {classification.error_type.value} on {request.branch}"
        description = self._build_description(classification, explanation)
        return await self.adapter.create_merge_request(
            request=request,
            fix_branch=fix_branch,
            title=title,
            description=description,
        )

    def _build_description(
        self,
        classification: ClassificationResult,
        explanation: str,
    ) -> str:
        files_section = "\n".join(f"- `{f}`" for f in classification.affected_files)
        return (
            f"## Automated fix by stitch\n\n"
            f"**Error type:** {classification.error_type.value}\n"
            f"**Confidence:** {classification.confidence:.0%}\n\n"
            f"### Affected files\n{files_section}\n\n"
            f"### Explanation\n{explanation}\n\n"
            f"---\n"
            f"*This MR was created automatically by [stitch-agent](https://github.com/stitch-agent). "
            f"Please review before merging.*"
        )
