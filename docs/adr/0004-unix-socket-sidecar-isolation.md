# ADR 0004: Unix-socket sidecar isolation

- Status: Superseded by ADR-0005 (the sidecar plugin tier is removed; its Unix-socket peer-authenticated transport is retained as the deckhand-wasm-host transport).
- Date: 2026-08-16

## Superseded

The sidecar-as-a-plugin-isolation-tier described below is gone. ADR-0005 makes the `wasm` tier the sole isolation tier (an in-process dev/read-only host plus the out-of-process `deckhand-wasm-host`), and there is no longer a `sidecar` lock source or runtime mode, no `deckhand-sidecar` entry point, and no plugin-as-a-sidecar loader. What lives on is precisely the transport this ADR designed: the length-prefixed JSON protocol, `SO_PEERCRED` peer authentication, and signed-artifact/digest/trust verification. That transport was re-homed to `deckhand.wasm_host_transport` (renamed `WasmHost*`) and is the peer-authenticated socket over which the broker reaches `deckhand-wasm-host`. Read the decision below as the historical rationale for that transport, not as a currently-selectable plugin tier.

## Context

Python entry points execute inside the broker and therefore share its process, dependencies, filesystem view, credentials, network access, and failure domain. Explicit activation and exact version locks make that tier suitable for curated read-only integrations, but they are not a security sandbox. Third-party or mutation-capable code needs a boundary that preserves Deckhand's durable lifecycle and reconciliation semantics without importing the plugin into a privileged process.

## Decision

Deckhand 0.5 adds sidecar protocol v1. A sidecar is selected with lock source `sidecar` and runtime mode `sidecar`; both the feature flag and an exact lock entry are required. The broker-side activation contains only transport and artifact-verification settings. Plugin configuration and credentials are loaded by the sidecar's own service and never enter the broker configuration.

The broker verifies all of the following before accepting a sidecar:

1. The artifact is a non-symlink, non-group/world-writable regular file owned by the configured deployment UID.
2. Its SHA-256 digest exactly matches the lock.
3. A configured Ed25519 trust key verifies a base64 detached signature over the ASCII lock digest.
4. Trust paths are absolute, non-symlink, non-group/world-writable, inside the configured trust root, and owned by the configured trust UID.
5. The Unix socket is under the configured socket root, has safe ancestor modes, is not a symlink or world-writable, and is owned by the expected plugin UID.
6. Kernel peer credentials match the expected UID. The sidecar independently checks the broker peer UID.
7. The handshake protocol, plugin ID, manifest/API/version, running artifact digest, declared adapters/actions, and contribution shape match the activation and lock.

Protocol messages are length-prefixed JSON with request IDs, a one-megabyte default limit, strict schemas, bounded nesting/cardinality/string sizes, and a fixed operation enum. Lifecycle results are parsed into the existing `Adapter*` and `StatusValue` models. Fields with secret-bearing names and non-finite numbers are rejected before they cross the boundary. Confirmation tokens are not sent to plugins. Sidecar exceptions are reduced to typed error, retry, and reconciliation classifications; raw exception messages/details never enter the broker.

`SidecarAdapter` and `SidecarStatusProvider` implement the existing protocols. The resilience wrapper remains outside those proxies, so deadlines, admission control, circuits, mutation timeout classification, worker recovery, observation-first reconciliation, and cancellation keep the same semantics for both isolation modes.

Production sidecars run as one Unix account and one hardened service per plugin. The public systemd template defaults to no network access and fixed CPU, memory, task, descriptor, filesystem, device, namespace, and syscall limits. A private site drop-in may add only the resolved destination CIDRs required by the manifest's logical egress bindings. Per-plugin secrets are added with service-specific systemd `LoadCredential=` directives.

## Consequences

- Enabled sidecars fail closed at broker startup when the artifact, signature, socket, peer, handshake, or manifest is unavailable or inconsistent.
- Disabled plugins are skipped before artifact or socket access. Removing both activation and lock entry leaves core and unrelated plugins startable.
- The broker does not launch, update, or clean stale sidecars. The service manager owns ordering, restart policy, credentials, resource controls, socket-directory lifecycle, and egress enforcement.
- Signature verification establishes publisher identity through the configured trust key; SHA-256 establishes the exact artifact. Release provenance and trust-key rotation remain deployment/release responsibilities.
- Unix peer credentials are required. Platforms without `SO_PEERCRED` or `getpeereid(2)` cannot enable this runtime.
- Hostname-based egress is intentionally not accepted as an enforcement claim. Private deployment resolves and pins approved destination CIDRs and reviews changes before updating `IPAddressAllow=`.

## Rejected alternatives

- **Import and then fork:** imports already execute plugin code in the broker and share its dependency graph.
- **Loopback HTTP:** expands the network attack surface and makes peer identity depend on another authentication mechanism.
- **One shared plugin host:** preserves a cross-plugin credential, dependency, resource, and crash domain.
- **Digest without signature:** identifies bytes but not their approved publisher.
- **Application-only resource or egress limits:** cannot contain a malicious or wedged process; controls belong to the service manager/kernel boundary.
