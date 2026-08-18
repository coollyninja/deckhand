import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .digests import confirmation_digest, request_digest
from .models import (
    ActionRequest,
    ConfirmationChallenge,
    ConfirmationMode,
    JobError,
    JobState,
    JobView,
    RetryDisposition,
    Subject,
)
from .state_machine import require_transition


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
  lease_owner TEXT,
  lease_expires_at TEXT,
  result_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state_created_idx ON jobs(state, created_at);
CREATE TABLE IF NOT EXISTS confirmations (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL,
  confirmation_digest TEXT NOT NULL,
  subject_name TEXT NOT NULL,
  subject_device TEXT NOT NULL,
  control TEXT,
  mode TEXT NOT NULL,
  expected_response TEXT,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL
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
        digest = request_digest(request)
        job_id = f"job_{uuid4().hex}"
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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

    def get_job_context(self, job_id: str) -> tuple[ActionRequest, Subject]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("job not found")
            return (
                ActionRequest.model_validate_json(row["request_json"]),
                Subject.model_validate_json(row["subject_json"]),
            )

    def claim_next_job(self, worker_id: str, lease_seconds: int = 30) -> JobView | None:
        now = datetime.now(UTC)
        lease_expiry = now + timedelta(seconds=lease_seconds)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM jobs
                WHERE state = ? AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                ORDER BY created_at LIMIT 1""",
                (JobState.QUEUED.value, now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            require_transition(JobState(row["state"]), JobState.RUNNING)
            connection.execute(
                """UPDATE jobs SET state = ?, lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ?""",
                (
                    JobState.RUNNING.value,
                    worker_id,
                    lease_expiry.isoformat(),
                    now.isoformat(),
                    row["id"],
                ),
            )
            self._append_audit(
                connection,
                "job.running",
                {"job_id": row["id"], "worker_id": worker_id},
            )
            claimed = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
            if claimed is None:
                raise StoreError("claimed job disappeared")
            return self._job_view(claimed)

    def transition_job(
        self,
        job_id: str,
        target: JobState,
        *,
        result: dict[str, Any] | None = None,
        error: JobError | None = None,
    ) -> JobView:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("job not found")
            require_transition(JobState(row["state"]), target)
            connection.execute(
                """UPDATE jobs SET state = ?, result_json = ?, error = ?,
                lease_owner = CASE WHEN ? = ? THEN lease_owner ELSE NULL END,
                lease_expires_at = CASE WHEN ? = ? THEN lease_expires_at ELSE NULL END,
                updated_at = ? WHERE id = ?""",
                (
                    target.value,
                    json.dumps(result, sort_keys=True) if result is not None else None,
                    error.model_dump_json() if error is not None else None,
                    target.value,
                    JobState.VERIFYING.value,
                    target.value,
                    JobState.VERIFYING.value,
                    now,
                    job_id,
                ),
            )
            self._append_audit(
                connection,
                f"job.{target.value}",
                {
                    "job_id": job_id,
                    "result": result,
                    "error": error.model_dump(mode="json") if error is not None else None,
                },
            )
            updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if updated is None:
                raise StoreError("updated job disappeared")
            return self._job_view(updated)

    def expire_leases(self) -> int:
        """Move abandoned remote operations to UNKNOWN_OUTCOME; never replay them blindly."""
        now = datetime.now(UTC)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM jobs WHERE state IN (?, ?)
                AND lease_expires_at IS NOT NULL AND lease_expires_at < ?""",
                (JobState.RUNNING.value, JobState.VERIFYING.value, now.isoformat()),
            ).fetchall()
            for row in rows:
                require_transition(JobState(row["state"]), JobState.UNKNOWN_OUTCOME)
                failure = JobError(
                    code="lease_expired",
                    message="worker lease expired; reconciliation required",
                    retry=RetryDisposition.RECONCILE_FIRST,
                    reconciliation_required=True,
                )
                connection.execute(
                    """UPDATE jobs SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                    error = ?, updated_at = ? WHERE id = ?""",
                    (
                        JobState.UNKNOWN_OUTCOME.value,
                        failure.model_dump_json(),
                        now.isoformat(),
                        row["id"],
                    ),
                )
                self._append_audit(
                    connection,
                    "job.unknown_outcome",
                    {"job_id": row["id"], "reason": "lease_expired"},
                )
            return len(rows)

    def list_jobs(self, state: JobState, limit: int = 100) -> list[JobView]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY updated_at LIMIT ?",
                (state.value, min(limit, 1000)),
            ).fetchall()
            return [self._job_view(row) for row in rows]

    def create_confirmation(
        self,
        request: ActionRequest,
        subject: Subject,
        mode: ConfirmationMode,
        prompt: str,
        ttl_seconds: int = 60,
    ) -> ConfirmationChallenge:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        confirmation_id = f"confirm_{uuid4().hex}"
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expected_response = request.target.id if mode == ConfirmationMode.TYPED else None
        digest = confirmation_digest(request)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO confirmations
                (id, token_hash, confirmation_digest, subject_name, subject_device, control,
                 mode, expected_response, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    confirmation_id,
                    token_hash,
                    digest,
                    subject.name,
                    subject.device,
                    request.context.control,
                    mode.value,
                    expected_response,
                    expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
            self._append_audit(
                connection,
                "confirmation.created",
                {
                    "confirmation_id": confirmation_id,
                    "confirmation_digest": digest,
                    "mode": mode.value,
                    "control": request.context.control,
                },
            )
        return ConfirmationChallenge(
            id=confirmation_id,
            token=token,
            mode=mode,
            expires_at=expires_at,
            prompt=prompt,
        )

    def consume_confirmation(
        self,
        request: ActionRequest,
        subject: Subject,
        token: str,
        response: str | None = None,
    ) -> bool:
        """Consume a confirmation for this request. Records the outcome, including
        rejections (wrong token / expired / wrong device / wrong control), so
        replay attempts are auditable rather than silent."""
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)
        digest = confirmation_digest(request)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM confirmations
                WHERE confirmation_digest = ? AND subject_name = ? AND subject_device = ?
                  AND used_at IS NULL ORDER BY created_at DESC LIMIT 1""",
                (digest, subject.name, subject.device),
            ).fetchone()
            reason = self._confirmation_reject_reason(row, token_hash, request, response, now)
            if reason is not None:
                self._append_audit(
                    connection,
                    "confirmation.rejected",
                    {
                        "confirmation_digest": digest,
                        "subject": subject.model_dump(),
                        "reason": reason,
                    },
                )
                return False
            connection.execute(
                "UPDATE confirmations SET used_at = ? WHERE id = ?", (now.isoformat(), row["id"])
            )
            self._append_audit(
                connection,
                "confirmation.consumed",
                {"confirmation_id": row["id"], "confirmation_digest": digest},
            )
            return True

    @staticmethod
    def _confirmation_reject_reason(
        row: sqlite3.Row | None,
        token_hash: str,
        request: ActionRequest,
        response: str | None,
        now: datetime,
    ) -> str | None:
        """Return None if the confirmation is valid, else a rejection reason."""
        if row is None:
            return "no_matching_confirmation"
        if not hmac.compare_digest(row["token_hash"], token_hash):
            return "token_mismatch"
        if datetime.fromisoformat(row["expires_at"]) <= now:
            return "expired"
        # Bind the physical control location: a confirmation issued for one key
        # cannot be consumed from another.
        if row["control"] != request.context.control:
            return "control_mismatch"
        if row["expected_response"] is not None and not hmac.compare_digest(
            row["expected_response"], response or ""
        ):
            return "typed_response_mismatch"
        return None

    def cancel_confirmation(self, confirmation_id: str, subject: Subject) -> bool:
        """Cancel a pending confirmation, bound to the issuing subject. Returns
        True if a live confirmation was cancelled, False otherwise."""
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM confirmations
                WHERE id = ? AND subject_name = ? AND subject_device = ? AND used_at IS NULL""",
                (confirmation_id, subject.name, subject.device),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE confirmations SET used_at = ? WHERE id = ?", (now, confirmation_id)
            )
            self._append_audit(
                connection,
                "confirmation.cancelled",
                {"confirmation_id": confirmation_id, "subject": subject.model_dump()},
            )
            return True

    def list_audit_events(self, after_sequence: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM audit_events WHERE sequence > ?
                ORDER BY sequence LIMIT ?""",
                (after_sequence, min(limit, 1000)),
            ).fetchall()
            return [dict(row) for row in rows]

    def verify_audit_chain(self) -> bool:
        previous_hash = "0" * 64
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False
            material = "\x1f".join(
                (previous_hash, row["occurred_at"], row["event_type"], row["payload_json"])
            )
            if not hmac.compare_digest(
                hashlib.sha256(material.encode()).hexdigest(), row["event_hash"]
            ):
                return False
            previous_hash = row["event_hash"]
        return True

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
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
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
            error=Store._job_error(row["error"]),
        )

    @staticmethod
    def _job_error(raw: str | None) -> JobError | None:
        if raw is None:
            return None
        try:
            return JobError.model_validate_json(raw)
        except ValueError:
            return JobError(code="legacy_error", message=raw)
