# ADR-0002: Stable plugin lifecycle and structured outcomes

- Status: accepted
- Date: 2026-08-14

## Decision

Plugin API v1 is stable for trusted in-process plugins. Every contributed adapter implements six operations: `health`, `plan`, `execute`, `observe`, `verify`, and `cancel`. Each operation exchanges Deckhand-owned models rather than plugin-specific exceptions or job states.

`execute` records an opaque execution reference and sanitized details. `observe` independently reads the target. `verify` compares the declared request, execution evidence, and current observation. A worker may report success only after verification is satisfied. Reconciliation repeats observation and verification; it does not replay execution.

Adapter failures carry a bounded error kind, retry disposition (`never`, `safe`, or `reconcile_first`), a reconciliation flag, and sanitized details. The core maps those values to durable job transitions. Plugins never import the store or select a `JobState`.

Cancellation is best effort and typed. `cancelled`, `not_supported`, `already_terminal`, and `unknown` are distinct; no ambiguous response is presented as success. Queued jobs cancel locally. Running, verifying, and unknown-outcome jobs delegate to their owning adapter and remain fail-closed.

## Consequences

- The plugin manager rejects adapters missing any lifecycle operation.
- Job errors are structured API objects while the SQLite column remains backward-compatible JSON text.
- Legacy plain-text database errors are read as `legacy_error` objects.
- Adapter health is available through authenticated `GET /v1/plugins/health` and does not leak exception messages.
- Status-provider `AdapterError` values normalize to unavailable status instead of becoming an unstructured HTTP 500.
- Plugin API v1 remains the trusted in-process tier. Process isolation is a separate host/runtime contract and does not change these domain models.
