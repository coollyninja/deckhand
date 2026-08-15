# Deckhand

Deckhand turns an Elgato Stream Deck into a trustworthy, extensible status and intent surface. The deck never holds infrastructure credentials or sends arbitrary commands. A hardened broker authenticates, authorizes, audits, executes, and verifies every remote action through version-locked `dh-*` plugins.

The implementation follows a source-controlled private deployment plan in the operator's Domain Vault. Public architecture and security invariants live under [`docs/`](docs/).

## Repository status

The implementation currently includes:

- strict action/request/status schemas;
- a FastAPI broker with durable SQLite jobs, idempotency, audit chaining, and fail-closed mutation checks;
- a catalog loader and deterministic fake adapter for development;
- a versioned plugin ABI, explicit activation configuration, and fail-closed plugin lock;
- a stable six-operation adapter lifecycle with structured errors, observation-before-success, reconciliation, health, and cancellation;
- central per-plugin deadlines, concurrency/rate limits, circuit breakers, sanitized exception handling, and resilience metrics;
- deny-by-default OPA policy and policy tests;
- leased durable worker execution and postcondition verification;
- normalized, explicitly stale/unconfigured status aggregation;
- `sdctl`, a localhost-only macOS agent, and Hammerspoon integration seam;
- an official-SDK Stream Deck plugin with status, typed action, and confirmation keys;
- hardened container/systemd/Caddy/launchd deployment assets;
- broker, policy, macOS-agent, plugin, container, and CI quality gates.

No production mutation is enabled by default.

## Plugin ecosystem

Plugin repositories use `dh-<slug>` names, expose the `deckhand.plugins` Python entry point, and namespace runtime components as `dh-<slug>.<component>`. Built-in and external plugins pass through the same manifest, configuration-schema, version-lock, and contribution validation. External plugin loading is disabled unless `DECKHAND_ALLOW_EXTERNAL_PLUGINS=true`.

Site topology belongs in an untracked `config/plugins.yaml` or a private deployment repository. Public solution packs contain logical aliases and placeholders only. See [Plugin architecture](docs/plugin-architecture.md).

The initial public ecosystem is:

- [`dh-http-status`](https://github.com/coollyninja/dh-http-status), the first independently installed read-only provider;
- [`dh-proxmox`](https://github.com/coollyninja/dh-proxmox), read-only Proxmox cluster, node, QEMU, and LXC observation;
- [`dh-prometheus`](https://github.com/coollyninja/dh-prometheus), read-only scalar, alert, and scrape-target observation;
- [`dh-plugin-template`](https://github.com/coollyninja/dh-plugin-template), a working GitHub template and conformance starter;
- [`dh-pack-homelab`](https://github.com/coollyninja/dh-pack-homelab), a topology-neutral observability composition.

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
