# ADR 0005: Ganglion WASM runtime tier

- Status: Proposed
- Date: 2026-08-19

## Context

Deckhand runs plugins in two isolation modes today. The trusted in-process Python entry-point tier ([ADR 0001](0001-plugin-and-pack-boundaries.md), [ADR 0002](0002-stable-plugin-lifecycle.md)) shares the broker's process, dependencies, credentials, and failure domain, and is suitable only for curated read-only integrations. Sidecar protocol v1 ([ADR 0004](0004-unix-socket-sidecar-isolation.md)) adds the mutation-capable boundary: a signed, digested artifact, a peer-authenticated Unix socket, per-plugin credentials, and systemd-enforced resource limits and default-deny egress.

That sidecar substrate is a bespoke, Linux-bound build of facilities Ganglion already generalizes. Ganglion v2.4.0 provides an Ed25519 `SignedManifest` over CBOR embedding a Blake3 component hash, a `TrustStore`, and a content-addressed version-pinned registry; a Wasmtime 36.0.13 component-model runtime with fuel metering, epoch and wall-clock deadlines, `StoreLimits` memory caps, and a deny-by-default WASI context that grants no ambient authority (sockets, env, and preopens denied unless a broker mediates); and a TOML default-deny policy engine enforced at both deploy-time over the whole manifest and per-call on every host call. Its capability contract is the WIT package `ganglion:capability@0.5.0`.

Two of Deckhand's ADR-0004 constraints are structural rather than incidental. The tier cannot run where `SO_PEERCRED`/`getpeereid(2)` is unavailable, so it does not reach macOS. Its egress claim is a resolved-CIDR pin in `IPAddressAllow=`, not an application-layer allowlist, and every Deckhand read-only plugin is in fact an HTTPS API client. Ganglion's no-ambient-authority model answers both, but three confirmed Ganglion gaps stand between the interface and Deckhand's needs, and each is generically useful to Ganglion regardless of Deckhand: (1) no HTTP/HTTPS egress capability — `network/probe` is ping/DNS/port-check/traceroute only, so a `ganglion:http/egress` capability group is required; (2) the WIT world exports a single `run(args)` entry, whereas the six-operation adapter lifecycle needs six named exports; (3) manifests model no credential-slot injection. These are upstream Ganglion work, tracked as Phase 2 of the transition strategy.

This ADR is a Phase-0 deliverable: it gets the tier decision reviewed before any substrate code lands, extending the tier model of ADR 0004 rather than replacing it. It formalizes the fit assessment and transition strategy verified against Ganglion v2.4.0 source.

## Decision

Deckhand adds a third plugin isolation mode, `wasm`, alongside the two existing modes. The three coexist and are selected per plugin:

- `in_process` — the curated read-only trusted tier of ADR 0001/0002. Unchanged by this decision, and untouched forever; there is no security payoff in forcing it to WASM.
- `sidecar` — sidecar protocol v1, the current mutation-capable isolation of ADR 0004.
- `wasm` — new: a Ganglion signed component executed under a capability broker with no ambient authority.

The `wasm` tier is gated by the feature flag `DECKHAND_ALLOW_WASM_PLUGINS`, which defaults to `false` and fails closed. As with the sidecar tier, enabling a `wasm` plugin requires both the flag and an exact lock entry; a disabled plugin is skipped before any artifact or runtime access.

**The seam.** `GanglionAdapter` and `GanglionStatusProvider` implement the existing `Adapter` and `StatusProvider` protocols and sit exactly where `SidecarAdapter` sits today: behind the adapter boundary and *inside* the resilience wrapper. The resilience guard of [ADR 0003](0003-read-plane-resilience.md) remains outside the proxy, so deadlines, admission control, circuits, mutation-timeout classification, worker recovery, observation-first reconciliation, and cancellation keep identical semantics across all three modes. Nothing above the adapter boundary changes: durable jobs, `observe`-before-success verification, `UNKNOWN_OUTCOME` handling, reconciliation, exact-request confirmation, and OPA intent authorization are untouched. The versioned `deckhand-adapter.wit` world (lifecycle v1 — the six ops `health/plan/execute/observe/verify/cancel`, `AdapterError` with its bounded kinds and retry/reconciliation dispositions, and `StatusValue`) is the tier contract, mirroring the frozen conformance suite exactly.

**Artifact and trust.** A `wasm` plugin is a `gang`-signed WASM component. The lock pins the component digest exactly as the sidecar lock pins a SHA-256; the digest+signature ceremony maps to `gang sign` plus the Ganglion trust store, and version locking maps to registry exact-version pins. The broker embeds the Ganglion runtime and invokes the six lifecycle exports by name. All host authority — egress, credentials, resource ceilings — is mediated by the deny-by-default capability broker and policy, never granted ambiently.

**The parity gate.** Each phase of the transition closes by mapping every row of the [threat-model](../threat-model.md) controls table to an equal-or-stronger control on the `wasm` tier. A single weaker row blocks the phase. The honest per-row reading:

- **Malicious/buggy plugin — stronger.** WASM has no ambient authority: a component cannot open a socket, read the filesystem, or touch the environment except through a broker that defaults deny and is evaluated per call. This is stronger than the sidecar's egress CIDR pins, and it is portable to macOS, where the sidecar's `SO_PEERCRED` peer-credential tier cannot run at all.
- **Lateral movement / egress — stronger.** The upstream `ganglion:http/egress` broker enforces a URL allowlist (host, path prefix, method) at the application layer, a path- and method-scoped claim that is strictly finer than systemd `IPAddressAllow=` resolved-CIDR pins.
- **Resource limits — stronger.** Wasmtime fuel metering plus epoch and wall-clock deadlines and `StoreLimits` memory caps are finer-grained than systemd CPU/memory/task quotas; syscall filtering is moot because no syscalls exist in the sandbox.
- **Process-boundary defense-in-depth — the one honest downgrade.** A Wasmtime escape lands in the broker process, whereas a sidecar escape lands in a separate UID. Mitigation: run the WASM host itself as the out-of-process sidecar, restoring the separate-process boundary on top of the sandbox — a double boundary while still deleting per-plugin Python packaging and locking. This in-process-vs-out-of-process choice is **deferred to a measured Phase-4 gate**, decided from the threat-model row and pilot operational evidence, not upfront doctrine.

**Sidecar v1 coexistence guarantee.** Sidecar protocol v1 is not deprecated until the `wasm` tier has shipped a full release with parity and every ported plugin has soaked one release. Even then, deprecation applies to the mutation tier only. The in-process curated read-only tier is never retired by this decision.

## Consequences

- A third isolation mode joins the plugin-configuration and lock schemas. Its trust root is the Ganglion trust store; its artifact is a `gang`-signed `.wasm` component whose digest the lock pins exactly.
- Enabled `wasm` plugins fail closed at broker startup when the flag is off, the artifact or signature is unavailable or inconsistent, the lock does not match, or the manifest and declared exports disagree. The default-off flag restores today's world instantly.
- The adapter boundary and resilience wrapper are the stable seam. A Ganglion-backed adapter is indistinguishable to jobs, verification, reconciliation, confirmation, and OPA from an in-process or sidecar adapter. No worker, store, confirmation, or policy code changes.
- The `wasm` tier is portable to hosts the sidecar tier cannot reach, because enforcement lives in the runtime, not the init system.
- The tier depends on three upstream Ganglion features (HTTP egress broker, named-export invocation, credential slots) landing as generic, released Ganglion capabilities. Deckhand pins released Ganglion versions only and blocks on none of them partially.
- The process-boundary row is the single deferred threat-model question. Until the Phase-4 measurement, the pilot runs the host in-process (single boundary), and the parity review records that row as explicitly pending rather than passed.
- Lifecycle-contract changes now require bumping the conformance version and updating the `deckhand-adapter.wit` draft in the same change, so contract drift across tiers stays visible.

## Rejected alternatives

- **Fork Ganglion's brokers into Deckhand.** Rejected: it recreates exactly the drift ADR 0001 exists to prevent and splits the security-review surface. Upstream-first keeps one reviewed implementation and lets Deckhand pin released versions.
- **Force the in-process Python tier onto WASM.** Rejected: ADR 0001 already scopes that curated read-only tier correctly, and sandboxing trusted read-only code buys no security. There is no payoff, only packaging cost.
- **Swap the transport layer (libp2p for Tailscale Serve/Caddy/mTLS) first.** Rejected for tailnet deployments: Deckhand's ingress-identity model is sound inside a tailnet, and Ganglion's end-to-end peer identity solves a different problem. Revisit only for off-tailnet deployments, which the `dh-ganglion` consumer plugin already serves without substrate surgery.
- **Application-only resource or egress limits without the sandbox boundary.** Rejected on the same grounds as ADR 0004: limits enforced only in application code cannot contain a malicious or wedged component. The `wasm` tier's controls are the runtime and broker boundary, not cooperative in-plugin checks.
