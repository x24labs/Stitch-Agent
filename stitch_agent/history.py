from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from stitch_agent.models import FixRequest, FixResult

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS fixes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo          TEXT    NOT NULL,
    branch        TEXT    NOT NULL DEFAULT '',
    error_type    TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    confidence    REAL    NOT NULL DEFAULT 0.0,
    reason        TEXT    NOT NULL DEFAULT '',
    fix_branch    TEXT,
    escalation_code TEXT,
    created_at    TEXT    NOT NULL
)
"""

_INSERT = """
INSERT INTO fixes
    (repo, branch, error_type, status, confidence, reason, fix_branch, escalation_code, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_RECENT = """
SELECT repo, branch, error_type, status, confidence, reason, fix_branch, escalation_code, created_at
FROM fixes
WHERE repo = ?
ORDER BY id DESC
LIMIT ?
"""

_PATTERN_QUERY = """
SELECT
    status,
    COUNT(*) as cnt,
    AVG(confidence) as avg_confidence
FROM fixes
WHERE repo = ? AND error_type = ?
GROUP BY status
"""


@dataclass(frozen=True)
class HistoryRecord:
    repo: str
    branch: str
    error_type: str
    status: str
    confidence: float
    reason: str
    fix_branch: str | None
    escalation_code: str | None
    created_at: str


@dataclass(frozen=True)
class PatternSummary:
    """Success/failure/escalation counts and average confidence for a (repo, error_type) pair."""

    repo: str
    error_type: str
    total: int
    fixed: int
    escalated: int
    errors: int
    avg_confidence: float

    @property
    def success_rate(self) -> float:
        return self.fixed / self.total if self.total else 0.0


class HistoryStore:
    """SQLite-backed store for fix history."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def open(self) -> None:
        if self._conn is not None:
            return
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> HistoryStore:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        return self._conn  # type: ignore[return-value]

    # ------------------------------------------------------------------ #
    # writes                                                               #
    # ------------------------------------------------------------------ #

    def record(self, request: FixRequest, result: FixResult) -> None:
        """Persist a single fix attempt."""
        now = datetime.now(UTC).isoformat()
        self._db.execute(
            _INSERT,
            (
                request.project_id,
                request.branch,
                result.error_type.value,
                result.status,
                result.confidence,
                result.reason,
                result.fix_branch,
                result.escalation_reason_code,
                now,
            ),
        )
        self._db.commit()

    # ------------------------------------------------------------------ #
    # reads                                                                #
    # ------------------------------------------------------------------ #

    def get_recent(self, repo: str, limit: int = 20) -> list[HistoryRecord]:
        """Return the most recent *limit* records for a repo."""
        rows = self._db.execute(_SELECT_RECENT, (repo, limit)).fetchall()
        return [HistoryRecord(**dict(row)) for row in rows]

    def get_pattern(self, repo: str, error_type: str) -> PatternSummary:
        """Return aggregated success/failure stats for a (repo, error_type) pair."""
        rows = self._db.execute(_PATTERN_QUERY, (repo, error_type)).fetchall()

        counts: dict[str, int] = {}
        avg_confidence = 0.0
        total_for_avg = 0

        for row in rows:
            counts[row["status"]] = row["cnt"]
            # weight avg_confidence by count
            avg_confidence += row["avg_confidence"] * row["cnt"]
            total_for_avg += row["cnt"]

        total = sum(counts.values())
        if total_for_avg:
            avg_confidence /= total_for_avg

        return PatternSummary(
            repo=repo,
            error_type=error_type,
            total=total,
            fixed=counts.get("fixed", 0),
            escalated=counts.get("escalate", 0),
            errors=counts.get("error", 0),
            avg_confidence=avg_confidence,
        )


def default_db_path(workspace_root: str = "/tmp/stitch-workspace") -> Path:
    return Path(workspace_root) / ".stitch" / "history.db"
