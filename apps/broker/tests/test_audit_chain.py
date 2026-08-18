"""Keyed, tamper-evident audit chain + verification + policy-denial auditing."""

import sqlite3
from pathlib import Path
from uuid import uuid4

from deckhand.models import ActionRequest, RequestContext, Subject, Target
from deckhand.store import Store


def _request() -> ActionRequest:
    return ActionRequest(
        action_id="test.resource.observe",
        action_version=1,
        target=Target(type="resource", id="example"),
        context=RequestContext(client="mac", control="main:r1c1"),
        idempotency_key=uuid4(),
    )


def _subject() -> Subject:
    return Subject(name="operator", device="mac", channel="mgmt-mtls")


def test_keyed_chain_verifies(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db", audit_hmac_key=b"secret-key")
    store.initialize()
    store.create_job(_request(), _subject())
    assert store.verify_audit_chain()


def test_keyed_chain_detects_payload_forgery(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    store = Store(path, audit_hmac_key=b"secret-key")
    store.initialize()
    store.create_job(_request(), _subject())
    # Tamper directly in the DB (a party with DB write access).
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE audit_events SET payload_json = ? WHERE sequence = "
            "(SELECT MIN(sequence) FROM audit_events)",
            ('{"forged": true}',),
        )
        conn.commit()
    assert not store.verify_audit_chain()


def test_attacker_cannot_recompute_keyed_chain(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    store = Store(path, audit_hmac_key=b"the-real-key")
    store.initialize()
    store.create_job(_request(), _subject())
    # An attacker with DB write but WITHOUT the key rewrites a payload and tries to
    # recompute the chain using an unkeyed SHA-256 (the best they can do).
    import hashlib

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
        previous = "0" * 64
        for row in rows:
            forged_payload = '{"forged": true}'
            material = "\x1f".join(
                (
                    str(row["sequence"]),
                    previous,
                    row["occurred_at"],
                    row["event_type"],
                    forged_payload,
                )
            ).encode()
            forged_hash = hashlib.sha256(material).hexdigest()
            conn.execute(
                "UPDATE audit_events SET payload_json = ?, event_hash = ? WHERE sequence = ?",
                (forged_payload, forged_hash, row["sequence"]),
            )
            previous = forged_hash
        conn.commit()
    # Verification with the real key rejects the attacker's unkeyed recomputation.
    assert not store.verify_audit_chain()


def test_unkeyed_chain_still_verifies_for_backward_compat(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")  # no key
    store.initialize()
    store.create_job(_request(), _subject())
    assert store.verify_audit_chain()


def test_policy_denial_is_audited(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db", audit_hmac_key=b"k")
    store.initialize()
    store.record_policy_denial(_request(), _subject(), "execute", "mutations are disabled")
    events = store.list_audit_events()
    denial = [e for e in events if e["event_type"] == "policy.denied"]
    assert len(denial) == 1
    assert store.verify_audit_chain()


def test_audit_write_probe_detects_readonly(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    store = Store(path)
    store.initialize()
    assert store.audit_is_writable() is True
    # Make the database file read-only; the write-probe must now report False even
    # though a SELECT would still succeed.
    path.chmod(0o444)
    try:
        # A read-only main db file blocks the INSERT probe.
        assert store.audit_is_writable() is False
    finally:
        path.chmod(0o644)
