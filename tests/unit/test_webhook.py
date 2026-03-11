from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from runners.webhook import _check_rate_limit, _rate_buckets, _verify_api_key, app


@pytest.fixture(autouse=True)
def clear_rate_buckets():
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestApiKeyAuth:
    def test_no_api_keys_configured_passes(self) -> None:
        from unittest.mock import MagicMock

        req = MagicMock()
        req.headers = {}
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = ""
            assert _verify_api_key(req) is True

    def test_valid_bearer_token_passes(self) -> None:
        from unittest.mock import MagicMock

        req = MagicMock()
        req.headers = {"Authorization": "Bearer secret123"}
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = "secret123,other"
            assert _verify_api_key(req) is True

    def test_invalid_bearer_token_rejected(self) -> None:
        from unittest.mock import MagicMock

        req = MagicMock()
        req.headers = {"Authorization": "Bearer wrong"}
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = "secret123"
            assert _verify_api_key(req) is False

    def test_missing_auth_header_rejected(self) -> None:
        from unittest.mock import MagicMock

        req = MagicMock()
        req.headers = {}
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = "secret123"
            assert _verify_api_key(req) is False

    def test_webhook_returns_401_on_bad_key(self, client: TestClient) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = "goodkey"
            s.webhook_rate_limit = 60
            s.webhook_rate_window = 60
            s.webhook_secret = ""
            resp = client.post(
                "/webhook/gitlab",
                headers={"Authorization": "Bearer badkey"},
                json={},
            )
        assert resp.status_code == 401


class TestRateLimiting:
    def test_within_limit_passes(self) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_rate_limit = 5
            s.webhook_rate_window = 60
            for _ in range(5):
                assert _check_rate_limit("10.0.0.1") is True

    def test_exceeds_limit_rejected(self) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_rate_limit = 3
            s.webhook_rate_window = 60
            for _ in range(3):
                _check_rate_limit("10.0.0.2")
            assert _check_rate_limit("10.0.0.2") is False

    def test_different_ips_independent(self) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_rate_limit = 1
            s.webhook_rate_window = 60
            assert _check_rate_limit("10.0.0.1") is True
            assert _check_rate_limit("10.0.0.2") is True

    def test_webhook_returns_429_on_rate_limit(self, client: TestClient) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 0
            s.webhook_rate_window = 60
            s.webhook_secret = ""
            resp = client.post("/webhook/gitlab", json={})
        assert resp.status_code == 429


class TestGitLabWebhook:
    def _payload(self, status: str = "failed") -> dict:
        return {
            "object_kind": "pipeline",
            "object_attributes": {"id": 42, "status": status, "ref": "main"},
            "project": {"id": 1},
            "builds": [{"id": 99, "status": "failed", "name": "test"}],
        }

    def test_ignores_non_pipeline_events(self, client: TestClient) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = ""
            resp = client.post("/webhook/gitlab", json={"object_kind": "push"})
        assert resp.json()["status"] == "ignored"

    def test_ignores_non_failed_pipelines(self, client: TestClient) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = ""
            resp = client.post("/webhook/gitlab", json=self._payload(status="success"))
        assert resp.json()["status"] == "ignored"

    def test_accepts_failed_pipeline_with_mock(self, client: TestClient) -> None:
        with (
            patch("runners.webhook._settings") as s,
            patch("runners.webhook._run_gitlab_fix", new_callable=AsyncMock),
        ):
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = ""
            resp = client.post("/webhook/gitlab", json=self._payload())
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["jobs_queued"] == 1

    def test_gitlab_hmac_rejected(self, client: TestClient) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = "mysecret"
            resp = client.post(
                "/webhook/gitlab",
                json=self._payload(),
                headers={"X-Gitlab-Token": "wrong"},
            )
        assert resp.status_code == 403

    def test_gitlab_hmac_accepted(self, client: TestClient) -> None:
        with (
            patch("runners.webhook._settings") as s,
            patch("runners.webhook._run_gitlab_fix", new_callable=AsyncMock),
        ):
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = "mysecret"
            resp = client.post(
                "/webhook/gitlab",
                json=self._payload(),
                headers={"X-Gitlab-Token": "mysecret"},
            )
        assert resp.json()["status"] == "accepted"


class TestGitHubWebhook:
    def _payload(self) -> dict:
        return {
            "action": "completed",
            "workflow_run": {
                "id": 123,
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "abc123",
            },
            "repository": {"full_name": "org/repo"},
        }

    def _sign(self, secret: str, body: bytes) -> str:
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={sig}"

    def test_ignores_non_workflow_run_events(self, client: TestClient) -> None:
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = ""
            resp = client.post(
                "/webhook/github",
                json=self._payload(),
                headers={"X-GitHub-Event": "push"},
            )
        assert resp.json()["status"] == "ignored"

    def test_accepts_failed_workflow(self, client: TestClient) -> None:
        with (
            patch("runners.webhook._settings") as s,
            patch("runners.webhook._run_github_fix", new_callable=AsyncMock),
        ):
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = ""
            resp = client.post(
                "/webhook/github",
                json=self._payload(),
                headers={"X-GitHub-Event": "workflow_run"},
            )
        assert resp.json()["status"] == "accepted"

    def test_github_hmac_rejected(self, client: TestClient) -> None:
        body = json.dumps(self._payload()).encode()
        with patch("runners.webhook._settings") as s:
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = "mysecret"
            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-GitHub-Event": "workflow_run",
                    "X-Hub-Signature-256": "sha256=badsig",
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 403

    def test_github_hmac_accepted(self, client: TestClient) -> None:
        body = json.dumps(self._payload()).encode()
        with (
            patch("runners.webhook._settings") as s,
            patch("runners.webhook._run_github_fix", new_callable=AsyncMock),
        ):
            s.webhook_api_keys = ""
            s.webhook_rate_limit = 100
            s.webhook_rate_window = 60
            s.webhook_secret = "mysecret"
            sig = self._sign("mysecret", body)
            resp = client.post(
                "/webhook/github",
                content=body,
                headers={
                    "X-GitHub-Event": "workflow_run",
                    "X-Hub-Signature-256": sig,
                    "Content-Type": "application/json",
                },
            )
        assert resp.json()["status"] == "accepted"
