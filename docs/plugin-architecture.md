# Plugin architecture

Deckhand core is intentionally topology-neutral. Integrations are explicit, version-locked `dh-*` plugins; workflows and UI composition live in declarative solution packs; actual infrastructure bindings live in a private site overlay.

## Repository families

| Name | Purpose | Public |
|---|---|---|
| `deckhand` | Core control plane, plugin API, clients, security floor | Yes |
| `dh-plugin-template` | Reference plugin and conformance starter | Yes |
| `dh-<integration>` | One independently releasable integration | Yes by default |
| `dh-pack-<solution>` | Declarative composition and profile examples | Yes by default |
| `deckhand-site-<site>` | Real topology, allowlists, deployment bindings | No |

The initial public ecosystem is `dh-plugin-template`, `dh-http-status`, `dh-proxmox`, `dh-prometheus`, `dh-tailscale`, and `dh-pack-homelab`. Additional integrations should not receive repositories until their contract and ownership are concrete.

Solution packs are described by `packages/contracts/solution-pack.schema.json`. A pack pins required plugin versions, names its logical profiles, and references example activation, lock, and policy artifacts. It is composition—not executable code—and cannot grant authority beyond the private deployment's policy.

## Runtime loading

1. The broker reads `config/plugins.yaml`. A missing file enables only `dh-core`.
2. Every enabled plugin must have an exact entry in `config/plugins.lock.yaml`.
3. Built-ins resolve from the core factory map. External Python plugins resolve from the `deckhand.plugins` entry-point group only when `DECKHAND_ALLOW_EXTERNAL_PLUGINS=true`.
4. The loader compares plugin ID, plugin API version, manifest version, lock version, and installed distribution version.
5. JSON Schema validation runs before plugin code receives configuration.
6. The contribution merger rejects undeclared adapters, cross-plugin action ownership, duplicate components, and action-to-adapter mismatches.
7. Catalog loading rejects actions whose plugins or adapters are unavailable.

The API exposes the active sanitized manifests at `GET /v1/plugins`. Configuration values, credential references, lock digests, socket paths, trust paths, and peer UIDs are not returned. `packages/contracts/plugin-configuration.schema.json` publishes the activation, resilience, and isolation shape.

## Isolation tiers

`in_process` is the default and remains appropriate only for curated read-only code. `sidecar` is mandatory for third-party or mutation-capable plugins. It is separately disabled by default with `DECKHAND_ALLOW_SIDECAR_PLUGINS=false`.

The sidecar runtime never imports the plugin distribution into the broker. It verifies the locked SHA-256 digest and Ed25519 signature, authenticates both Unix peers, performs a strict handshake, then contributes proxy adapters and status providers behind the normal registry. Plugin-specific configuration and credentials are passed only to the independently supervised sidecar. See [ADR 0004](adr/0004-unix-socket-sidecar-isolation.md).

Public deployment assets default each sidecar to no network access and bounded CPU, memory, processes, file descriptors, filesystems, devices, namespaces, and syscalls. A private site overlay supplies a service account, broker UID, systemd credentials, and exact `IPAddressAllow=` entries corresponding to reviewed logical egress bindings. These enforcement bindings never belong in a public plugin or pack.

## Manifest contract

Every plugin declares:

- `id`, semantic `version`, and `api_version`;
- namespaced adapters and declared action IDs;
- status-provider types;
- whether mutation is possible;
- credential slots and logical egress bindings;
- a strict JSON Schema for configuration.

Plugin API v1 is stable for the trusted in-process tier. Adapters implement `health`, `plan`, `execute`, `observe`, `verify`, and `cancel` with Deckhand-owned models. The worker records execution evidence, observes independently, and requires a satisfied verification before success. Reconciliation observes and verifies without replaying execution.

Errors declare a bounded kind, retry disposition, reconciliation requirement, and sanitized details. Plugins never select job states or access the durable store. See [ADR-0002](adr/0002-stable-plugin-lifecycle.md) and `packages/contracts/adapter-lifecycle.schema.json`.

Authenticated operators can inspect normalized adapter health at `GET /v1/plugins/health` and request cancellation at `POST /v1/jobs/{job_id}:cancel`. Cancellation is subject/device-bound and never converts an unknown or unsupported outcome into success.

## Read-plane resilience

The plugin manager wraps every contributed adapter and status provider with one shared per-plugin guard. The guard enforces a total call deadline, maximum concurrency, token-bucket rate and burst, transient-failure circuit breaker, and single half-open recovery probe. This central placement gives workers, reconciliation, cancellation, status aggregation, and future clients the same behavior without changing plugin implementations.

Unexpected exceptions are sanitized. A timeout after mutation dispatch becomes `UnknownOutcome`; pre-dispatch and read timeouts remain safely retryable. Bounded-label Prometheus metrics cover outcomes, latency, local queueing, in-flight work, and circuit state. Authenticated operators can inspect sanitized snapshots at `GET /v1/plugins/resilience`. See [ADR-0003](adr/0003-read-plane-resilience.md).

## Configuration and secrets

Public plugin or pack examples may contain logical names and `.invalid` placeholders. They must not contain real addresses, DNS suffixes, resource IDs, usernames, certificate subjects, or organization-specific routing.

Private site configuration selects plugins and binds logical aliases. Secret values are delivered through systemd credentials, Keychain, or an approved secret manager. A plugin configuration may name a credential slot, but may not embed a token or password.

## Built-ins

`dh-core` ships with Deckhand and provides only deterministic development adapters. Curated integration plugins may later be installed in the release image, but they remain disabled until selected and locked. Bundling never bypasses plugin validation.

## Compatibility

- Plugin API changes are versioned independently from action schemas.
- Action definitions retain immutable integer versions.
- Plugin releases follow semantic versioning.
- The lock records exact versions; deployment promotion updates the lock intentionally.
- CI runs the same lifecycle contract through bundled, external in-process, and sidecar proxy paths.
- Additive model fields are backward-compatible within API v1. Removing lifecycle methods, changing enum meaning, or changing required fields requires a new plugin API version.
