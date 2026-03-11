from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stitch_agent.core.validator import Validator
from stitch_agent.models import StitchConfig


def test_trusted_mode_returns_passed() -> None:
    v = Validator(mode="trusted")
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(v.validate(Path("/tmp")))
    assert result.passed is True
    assert "Trusted" in result.output


@pytest.mark.asyncio
async def test_strict_mode_docker_not_installed() -> None:
    v = Validator(mode="strict")
    with patch.dict("sys.modules", {"docker": None}):
        result = await v.validate(Path("/tmp"))
    assert result.passed is False
    assert "docker" in result.output.lower()


@pytest.mark.asyncio
async def test_strict_mode_success() -> None:
    v = Validator(mode="strict")
    mock_docker = MagicMock()
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.return_value = b"2 passed\n"
    mock_client.containers.run.return_value = mock_container
    mock_docker.from_env.return_value = mock_client

    with patch.dict("sys.modules", {"docker": mock_docker}):
        result = await v.validate(Path("/tmp/worktree"))

    assert result.passed is True
    assert "passed" in result.output


@pytest.mark.asyncio
async def test_strict_mode_failure() -> None:
    v = Validator(mode="strict")
    mock_docker = MagicMock()
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.wait.return_value = {"StatusCode": 1}
    mock_container.logs.return_value = b"1 failed\n"
    mock_client.containers.run.return_value = mock_container
    mock_docker.from_env.return_value = mock_client

    with patch.dict("sys.modules", {"docker": mock_docker}):
        result = await v.validate(Path("/tmp/worktree"))

    assert result.passed is False


def test_resolve_image_from_config() -> None:
    config = StitchConfig(docker_image="my-custom:latest")
    v = Validator(mode="strict", config=config)
    assert v._resolve_image(Path("/tmp")) == "my-custom:latest"


def test_resolve_image_from_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM node:20-slim\nRUN npm install\n")
    v = Validator(mode="strict")
    assert v._resolve_image(tmp_path) == "node:20-slim"


def test_resolve_image_from_language(tmp_path: Path) -> None:
    config = StitchConfig(languages=["python"])
    v = Validator(mode="strict", config=config)
    assert v._resolve_image(tmp_path) == "python:3.12-slim"


def test_resolve_image_default(tmp_path: Path) -> None:
    v = Validator(mode="strict")
    assert v._resolve_image(tmp_path) == "python:3.12-slim"


def test_resolve_runner_from_config() -> None:
    config = StitchConfig(test_runner="pytest")
    v = Validator(mode="strict", config=config)
    assert v._resolve_runner(Path("/tmp")) == ["pytest", "--tb=short", "-q"]


def test_resolve_runner_bun_lockb(tmp_path: Path) -> None:
    (tmp_path / "bun.lockb").write_bytes(b"")
    v = Validator(mode="strict")
    assert v._resolve_runner(tmp_path) == ["bun", "test"]


def test_resolve_runner_go_mod(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com\n")
    v = Validator(mode="strict")
    assert v._resolve_runner(tmp_path) == ["go", "test", "./..."]


def test_resolve_runner_default(tmp_path: Path) -> None:
    v = Validator(mode="strict")
    assert v._resolve_runner(tmp_path) == ["pytest", "--tb=short", "-q"]
