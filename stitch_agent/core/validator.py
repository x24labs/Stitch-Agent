from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

    from stitch_agent.models import StitchConfig

logger = logging.getLogger("stitch.validator")

_LANGUAGE_IMAGES: dict[str, str] = {
    "python": "python:3.12-slim",
    "node": "node:20-slim",
    "javascript": "node:20-slim",
    "typescript": "node:20-slim",
    "go": "golang:1.22-alpine",
    "ruby": "ruby:3.3-slim",
}

_RUNNER_COMMANDS: dict[str, list[str]] = {
    "pytest": ["pytest", "--tb=short", "-q"],
    "bun": ["bun", "test"],
    "npm": ["npm", "test"],
    "yarn": ["yarn", "test"],
    "go": ["go", "test", "./..."],
    "rspec": ["rspec"],
}

_TIMEOUT_SECONDS = 300


class Validator:
    def __init__(
        self,
        mode: Literal["trusted", "strict"] = "trusted",
        config: StitchConfig | None = None,
    ) -> None:
        self.mode = mode
        self.config = config

    async def validate(self, worktree_path: Path) -> ValidationResult:
        if self.mode == "trusted":
            return ValidationResult(passed=True, output="Trusted mode: skipping validation")
        return await self._strict_validate(worktree_path)

    async def _strict_validate(self, worktree_path: Path) -> ValidationResult:
        try:
            import docker
        except ImportError:
            return ValidationResult(
                passed=False,
                output=(
                    "docker package not installed. Install with: pip install stitch-agent[strict]"
                ),
            )

        image = self._resolve_image(worktree_path)
        runner_cmd = self._resolve_runner(worktree_path)

        try:
            client = docker.from_env()
            container = client.containers.run(
                image=image,
                command=runner_cmd,
                volumes={str(worktree_path): {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                remove=False,
                detach=True,
                stdout=True,
                stderr=True,
            )
            try:
                exit_info = container.wait(timeout=_TIMEOUT_SECONDS)
                logs = container.logs(stdout=True, stderr=True)
                text = logs.decode() if isinstance(logs, bytes) else str(logs)
                exit_code = exit_info.get("StatusCode", 1)
                return ValidationResult(passed=exit_code == 0, output=text)
            finally:
                container.remove(force=True)
        except Exception as exc:
            return ValidationResult(passed=False, output=str(exc))

    def _resolve_image(self, worktree_path: Path) -> str:
        if self.config and self.config.docker_image:
            return self.config.docker_image

        dockerfile = worktree_path / "Dockerfile"
        if dockerfile.exists():
            raw = dockerfile.read_text()
            first_line = raw.splitlines()[0] if raw else ""
            if first_line.startswith("FROM "):
                return first_line[5:].strip()

        if self.config and self.config.languages:
            lang = self.config.languages[0].lower()
            if lang in _LANGUAGE_IMAGES:
                return _LANGUAGE_IMAGES[lang]

        return "python:3.12-slim"

    def _resolve_runner(self, worktree_path: Path) -> list[str]:
        if self.config and self.config.test_runner:
            runner = self.config.test_runner.lower()
            return _RUNNER_COMMANDS.get(runner, [self.config.test_runner])

        if (worktree_path / "bun.lockb").exists():
            return _RUNNER_COMMANDS["bun"]
        if (worktree_path / "package.json").exists():
            return _RUNNER_COMMANDS["npm"]
        if (worktree_path / "go.mod").exists():
            return _RUNNER_COMMANDS["go"]
        return _RUNNER_COMMANDS["pytest"]


class ValidationResult:
    def __init__(self, passed: bool, output: str) -> None:
        self.passed = passed
        self.output = output
