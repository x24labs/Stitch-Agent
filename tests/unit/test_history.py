from __future__ import annotations

import pytest

from stitch_agent.history import HistoryStore, default_db_path
from stitch_agent.models import ErrorType, FixRequest, FixResult


def _req(project_id: str = "org/repo", branch: str = "main") -> FixRequest:
    return FixRequest(
        platform="gitlab",
        project_id=project_id,
        pipeline_id="99",
        job_id="1",
        branch=branch,
    )


def _result(
    status: str = "fixed",
    error_type: ErrorType = ErrorType.LINT,
    confidence: float = 0.9,
    escalation_code: str | None = None,
) -> FixResult:
    return FixResult(
        status=status,  # type: ignore[arg-type]
        error_type=error_type,
        confidence=confidence,
        reason="auto",
        escalation_reason_code=escalation_code,
    )


@pytest.fixture
def store() -> HistoryStore:
    s = HistoryStore(":memory:")
    s.open()
    return s


class TestHistoryStoreRecord:
    def test_record_and_retrieve(self, store: HistoryStore) -> None:
        store.record(_req(), _result())
        records = store.get_recent("org/repo")
        assert len(records) == 1
        r = records[0]
        assert r.repo == "org/repo"
        assert r.branch == "main"
        assert r.error_type == "lint"
        assert r.status == "fixed"
        assert r.confidence == pytest.approx(0.9)

    def test_multiple_records_ordered_newest_first(self, store: HistoryStore) -> None:
        store.record(_req(branch="b1"), _result(confidence=0.7))
        store.record(_req(branch="b2"), _result(confidence=0.8))
        store.record(_req(branch="b3"), _result(confidence=0.9))
        records = store.get_recent("org/repo")
        assert [r.branch for r in records] == ["b3", "b2", "b1"]

    def test_limit_respected(self, store: HistoryStore) -> None:
        for i in range(10):
            store.record(_req(branch=f"b{i}"), _result())
        records = store.get_recent("org/repo", limit=3)
        assert len(records) == 3

    def test_isolation_between_repos(self, store: HistoryStore) -> None:
        store.record(_req(project_id="org/a"), _result())
        store.record(_req(project_id="org/b"), _result())
        assert len(store.get_recent("org/a")) == 1
        assert len(store.get_recent("org/b")) == 1
        assert store.get_recent("org/c") == []

    def test_escalation_fields_stored(self, store: HistoryStore) -> None:
        store.record(
            _req(),
            _result(status="escalate", escalation_code="low_confidence"),
        )
        records = store.get_recent("org/repo")
        assert records[0].escalation_code == "low_confidence"
        assert records[0].status == "escalate"


class TestHistoryStorePattern:
    def test_empty_pattern(self, store: HistoryStore) -> None:
        summary = store.get_pattern("org/repo", "lint")
        assert summary.total == 0
        assert summary.success_rate == 0.0

    def test_all_fixed(self, store: HistoryStore) -> None:
        for _ in range(4):
            store.record(_req(), _result(status="fixed", confidence=0.9))
        summary = store.get_pattern("org/repo", "lint")
        assert summary.total == 4
        assert summary.fixed == 4
        assert summary.escalated == 0
        assert summary.errors == 0
        assert summary.success_rate == pytest.approx(1.0)

    def test_mixed_outcomes(self, store: HistoryStore) -> None:
        store.record(_req(), _result(status="fixed"))
        store.record(_req(), _result(status="fixed"))
        store.record(_req(), _result(status="escalate", escalation_code="low_confidence"))
        store.record(_req(), _result(status="error"))
        summary = store.get_pattern("org/repo", "lint")
        assert summary.total == 4
        assert summary.fixed == 2
        assert summary.escalated == 1
        assert summary.errors == 1
        assert summary.success_rate == pytest.approx(0.5)

    def test_pattern_isolated_by_error_type(self, store: HistoryStore) -> None:
        store.record(_req(), _result(status="fixed", error_type=ErrorType.LINT))
        store.record(_req(), _result(status="escalate", error_type=ErrorType.COMPLEX_TYPE))
        lint_summary = store.get_pattern("org/repo", "lint")
        complex_summary = store.get_pattern("org/repo", "complex_type")
        assert lint_summary.fixed == 1
        assert complex_summary.escalated == 1

    def test_avg_confidence(self, store: HistoryStore) -> None:
        store.record(_req(), _result(status="fixed", confidence=0.8))
        store.record(_req(), _result(status="fixed", confidence=1.0))
        summary = store.get_pattern("org/repo", "lint")
        assert summary.avg_confidence == pytest.approx(0.9)


class TestHistoryStoreContextManager:
    def test_context_manager_opens_and_closes(self) -> None:
        with HistoryStore(":memory:") as store:
            store.record(_req(), _result())
            assert len(store.get_recent("org/repo")) == 1
        assert store._conn is None


class TestDefaultDbPath:
    def test_default_path(self) -> None:
        path = default_db_path("/tmp/stitch-workspace")
        assert str(path) == "/tmp/stitch-workspace/.stitch/history.db"
