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

The initial public ecosystem is `dh-plugin-template`, `dh-http-status`, and `dh-pack-homelab`. Additional integrations should not receive repositories until their contract and ownership are concrete.

Solution packs are described by `packages/contracts/solution-pack.schema.json`. A pack pins required plugin versions, names its logical profiles, and references example activation, lock, and policy artifacts. It is composition—not executable code—and cannot grant authority beyond the private deployment's policy.

## Runtime loading

1. The broker reads `config/plugins.yaml`. A missing file enables only `dh-core`.
2. Every enabled plugin must have an exact entry in `config/plugins.lock.yaml`.
3. Built-ins resolve from the core factory map. External Python plugins resolve from the `deckhand.plugins` entry-point group only when `DECKHAND_ALLOW_EXTERNAL_PLUGINS=true`.
4. The loader compares plugin ID, plugin API version, manifest version, lock version, and installed distribution version.
5. JSON Schema validation runs before plugin code receives configuration.
6. The contribution merger rejects undeclared adapters, cross-plugin action ownership, duplicate components, and action-to-adapter mismatches.
7. Catalog loading rejects actions whose plugins or adapters are unavailable.

The API exposes the active sanitized manifests at `GET /v1/plugins`. Configuration values, credential references, and lock digests are not returned.

## Manifest contract

Every plugin declares:

- `id`, semantic `version`, and `api_version`;
- namespaced adapters and declared action IDs;
- status-provider types;
- whether mutation is possible;
- credential slots and logical egress bindings;
- a strict JSON Schema for configuration.

Adapters implement `plan`, `execute`, and `verify` today. The stable API will add `health`, `observe`, `cancel`, and explicit reconciliation/error classification before API version 1 is declared stable.

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
- CI runs the same conformance suite against bundled and external plugins.
