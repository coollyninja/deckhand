import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import ActionRequest, JobState, JobView, Subject


class StoreError(RuntimeError):
    pass


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_json TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  subject_json TEXT NOT NULL,
  state TEXT NOT NULL,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL UNIQUE
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise StoreError(str(error)) from error
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def create_job(self, request: ActionRequest, subject: Subject) -> JobView:
        now = datetime.now(UTC).isoformat()
        request_json = request.model_dump_json()
        digest = hashlib.sha256(request_json.encode()).hexdigest()
        job_id = f"job_{uuid4().hex}"
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (str(request.idempotency_key),)
            ).fetchone()
            if existing:
                if existing["request_digest"] != digest:
                    raise StoreError("idempotency key reused with a different request")
                return self._job_view(existing)
            connection.execute(
                """INSERT INTO jobs
                (id, idempotency_key, request_json, request_digest, subject_json, state,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    str(request.idempotency_key),
                    request_json,
                    digest,
                    subject.model_dump_json(),
                    JobState.QUEUED.value,
                    now,
                    now,
                ),
            )
            self._append_audit(
                connection,
                "job.queued",
                {"job_id": job_id, "request_digest": digest, "subject": subject.model_dump()},
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("job write did not persist")
            return self._job_view(row)

    def get_job(self, job_id: str) -> JobView | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return None if row is None else self._job_view(row)

    def audit_is_writable(self) -> bool:
        try:
            with self.connect() as connection:
                connection.execute(
                    "SELECT sequence FROM audit_events ORDER BY sequence DESC LIMIT 1"
                )
            return True
        except StoreError:
            return False

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection, event_type: str, payload: dict[str, Any]
    ) -> None:
        previous = connection.execute(
            "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else "0" * 64
        occurred_at = datetime.now(UTC).isoformat()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        material = "\x1f".join((previous_hash, occurred_at, event_type, payload_json))
        event_hash = hashlib.sha256(material.encode()).hexdigest()
        connection.execute(
            """INSERT INTO audit_events
            (occurred_at, event_type, payload_json, previous_hash, event_hash)
            VALUES (?, ?, ?, ?, ?)""",
            (occurred_at, event_type, payload_json, previous_hash, event_hash),
        )

    @staticmethod
    def _job_view(row: sqlite3.Row) -> JobView:
        request = ActionRequest.model_validate_json(row["request_json"])
        return JobView(
            id=row["id"],
            state=JobState(row["state"]),
            action_id=request.action_id,
            target=request.target,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
        )
