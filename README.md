# Deckhand

Deckhand turns an Elgato Stream Deck into a trustworthy status and intent surface for a macOS workstation and the Tafy Labs lab. The deck never holds infrastructure credentials or sends arbitrary commands. A hardened broker authenticates, authorizes, audits, executes, and verifies every remote action.

The implementation follows a source-controlled private deployment plan in the operator's Domain Vault. Public architecture and security invariants live under [`docs/`](docs/).

## Repository status

The contract-first foundation includes:

- strict action/request/status schemas;
- a FastAPI broker with durable SQLite jobs, idempotency, audit chaining, and fail-closed mutation checks;
- a catalog loader and deterministic fake adapter for development;
- deny-by-default OPA policy and policy tests;
- broker unit/API tests and CI quality gates;
- deployment and client directories reserved for production workstreams.

No production mutation is enabled by default.

## Development

Requirements: Python 3.12+, `uv`, Node.js 22+, pnpm 10+, and OPA for policy tests.

```bash
uv sync --all-groups
uv run deckhand-api
uv run pytest
uv run ruff check .
uv run mypy apps/broker/src
```

The API binds to `127.0.0.1:19470` unless explicitly configured otherwise. Development identity headers are accepted only when `DECKHAND_TRUSTED_PROXY=true`; never enable that setting on a directly reachable listener.

## Security

See [SECURITY.md](SECURITY.md). Report vulnerabilities privately. Do not include credentials, internal addresses beyond the documented examples, or exploit details in public issues.
