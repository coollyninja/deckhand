# ADR-0001: Plugin, pack, and site boundaries

- Status: accepted
- Date: 2026-08-14

## Decision

Deckhand is divided into four layers:

1. **Core** owns transport, identity, policy orchestration, durable jobs, audit, plugin loading, and client contracts. It contains no environment domains, endpoints, resource IDs, or vendor actions.
2. **Plugins** are executable `dh-<slug>` integrations. They contribute namespaced adapters, status providers, and optionally action definitions through the versioned plugin API.
3. **Solution packs** are declarative `dh-pack-<slug>` repositories. They compose plugins, action catalogs, policy additions, dashboards, runbooks, and Stream Deck profile templates using logical aliases and placeholders.
4. **Site overlays** are private. They bind logical aliases to real endpoints and resources, select exact plugin versions, provide OPA data, and reference externally managed credentials.

Built-in plugins are distribution conveniences, not privileged exceptions. They use the same manifest, activation, version lock, schema validation, namespace checks, and contribution merger as installed plugins.

## Naming

- Repository and plugin ID: `dh-<slug>`.
- Pack ID: `dh-pack-<slug>`.
- Python import: `dh_<slug>`.
- Python entry-point group: `deckhand.plugins`; entry-point name equals the plugin ID.
- Runtime adapter ID: `dh-<slug>.<component>`.
- Action IDs remain domain-oriented and globally versioned, while every action explicitly declares its owning plugin.
- `dh-core` is reserved for the topology-neutral built-in development plugin.

## Security consequences

- No plugin is loaded merely because it is installed. It must be enabled and version-locked.
- Installed plugins require an explicit external-plugin switch. Production mutation plugins additionally require artifact verification and process isolation before activation.
- Plugin configuration is validated against the manifest's JSON Schema before plugin code receives it.
- A plugin can contribute only declared, namespaced adapters and declared actions.
- Catalog startup fails when an action refers to a missing plugin or adapter.
- Core policy is a non-bypassable floor. Plugin and site policy may narrow authority, never expand it beyond core invariants.
- Secret values are not plugin configuration. Config contains credential slot names or file references whose values are supplied to the isolated runtime by deployment tooling.

## Current trust tier

Version 0.2 implemented the trusted in-process Python entry-point tier. External loading remains off by default. Version 0.5 adds the Unix-socket sidecar host, signed artifact verification, per-plugin credential boundary, resource controls, and default-deny egress described in [ADR 0004](0004-unix-socket-sidecar-isolation.md). Third-party or mutation-capable plugins must use that tier before production activation.

## Alternatives rejected

- Hardcoded adapters in each broker process: causes drift and makes public releases inherit one site's assumptions.
- Automatic entry-point discovery and activation: installation would silently grant code execution.
- One repository per deployment: mixes reusable integration code with topology and secrets.
- Arbitrary commands in configuration: bypasses typed actions, policy, audit, and postcondition verification.
