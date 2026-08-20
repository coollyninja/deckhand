# Threat model

## Assets

Deckhand protects infrastructure credentials, action authority, operator/device identity, policy and catalog integrity, job/audit evidence, target availability, and truthful control-surface state. Physical safety remains outside Deckhand's authority.

## Trust boundaries

1. Stream Deck event → plugin process.
2. Plugin/CLI → Tailscale Serve or management mTLS.
3. Local Caddy identity boundary → loopback-only broker.
4. Broker → OPA and SQLite.
5. Worker → purpose-scoped target APIs or forced-command wrappers.
6. Status sources → normalized state shown to the operator.

## Primary threats and controls

| Threat | Controls | Required evidence |
|---|---|---|
| Malicious/buggy plugin | signed/digested sidecar artifact, peer-authenticated Unix socket, strict bounded protocol, per-plugin credentials, service resource limits, default-deny egress | tamper/peer/frame/secret-field tests; lifecycle proxy tests; unit hardening review |
| Spoofed proxy headers | loopback listener, proxy assertion, Serve-stripped identity headers, app capabilities, mTLS | direct/spoof tests return 401 |
| Accidental press | ensure-state semantics, exact-request confirmation, separate danger UI | confirmation binding/replay tests |
| Replay/race | UUID idempotency, request digest, single-use token, immediate transaction | concurrency and changed-request tests |
| Broker compromise | dedicated VM, minimal image, non-root services, systemd sandbox, separate credentials | hardening audit and container scan |
| Credential disclosure | systemd credentials, per-purpose files/tokens, no secret settings, redaction | repository/log/profile scans; rotation drill |
| Remote timeout | `UNKNOWN_OUTCOME`, no blind retry, source-state reconciliation | worker-kill and timeout tests |
| Misleading key state | observed state, source timestamps, stale/unconfigured states | source-loss acceptance tests |
| Policy/audit outage | readiness failure and mutation fail-closed | dependency-outage tests |
| Prompt injection | AI may propose typed plans only; normal policy and confirmation still apply | adversarial plan corpus |
| Lateral movement | one management NIC, egress allowlists, no public ingress, scoped target roles | firewall review and network probes |
| Physical hazard | only status/safe-state requests; protected resources excluded; physical controls authoritative | protected-resource matrix review |

## `wasm` isolation-tier parity (ADR-0005)

The controls above describe the `sidecar` isolation tier. ADR-0005's parity gate
requires every isolation-sensitive row to map to an **equal-or-stronger** control on
the `wasm` tier before a phase lands; a single weaker row blocks the phase. The rows
the isolation boundary touches, and the honest reading (parity review current as of
Phase 4 — the out-of-process host `deckhand-wasm-host` exists in-tree behind the
default-off `DECKHAND_ALLOW_WASM_PLUGINS`, built and tested, production soak pending):

| Threat (row above) | `wasm`-tier control | Verdict | Evidence |
|---|---|---|---|
| Malicious/buggy plugin | Signed/digested `gang` component under a no-ambient-authority WASI ctx (sockets/env/preopens denied unless a per-call default-deny broker mediates); digest pinned in the lock, publisher via the Ganglion trust store | **Stronger** — no ambient authority at all vs the sidecar's egress-CIDR pins; also portable to macOS, where the sidecar's `SO_PEERCRED` tier cannot run | frozen conformance suite passes identically on the tier; `test_ganglion.py`, `test_wasm_host.py`; real `gang run` instantiation smoke of the pilot |
| Lateral movement / egress | `ganglion:http/egress` URL allowlist (host + path-prefix + method) enforced host-side, re-validated per call; non-GET/HEAD requires an endpoint signed `:rw` | **Stronger** — application-layer path/method scoping is strictly finer than systemd `IPAddressAllow=` resolved-CIDR pins | egress observed live in the three plugin smoke tests; `:rw` requirement proven load-bearing by a negative control (read-only endpoint blocks POST) |
| Resource limits | Wasmtime fuel metering + epoch and wall-clock deadlines + `StoreLimits` memory caps; set in the signed manifest (`gang sign --cpu-fuel/--wall-clock-secs/--max-memory-bytes`) | **Stronger** — finer-grained than systemd CPU/memory/task quotas; syscall filtering is moot (no syscalls in the sandbox) | real `gang run` honours the manifest `cpu_fuel`; fuel-exhaustion observed and cleared via the flag |
| Process-boundary defense-in-depth | The out-of-process `deckhand-wasm-host` runs the runtime under its own UID + the hardened systemd unit, reached over the ADR-0004 peer-authenticated socket — a **double boundary** (Wasmtime sandbox *inside* a separate-UID process). Mutation-capable `wasm` plugins require it; the in-process host is a dev/read-only convenience that fails closed for mutation | **Equal-or-stronger** — separate UID **and** the WASM no-ambient-authority guarantee the sidecar lacks | `deckhand-wasm-host` passes the frozen conformance suite over a real peer-authenticated socket; peer-UID mismatch rejected; mutation-over-in-process gate fails closed (`test_wasm_host.py`) — production soak still pending |

Non-isolation rows (spoofed proxy headers, accidental press, replay/race, broker
compromise, credential disclosure, remote timeout, misleading key state, policy/audit
outage, prompt injection, physical hazard) are tier-independent: they live above the
adapter boundary (identity, confirmation, idempotency, reconciliation, OPA, the broker
VM) and are unchanged by the isolation mode. Credential disclosure additionally gains
on the `wasm` tier — secrets arrive via Ganglion credential slots injected into the
otherwise-empty WASI env, never in manifests, policy, logs, or events.

## Non-negotiable assumptions

- Caddy and Tailscale Serve run on the same host as the loopback broker.
- The proxy assertion is readable only by the proxy and broker services.
- App capability data is authored in Tailnet grants and injected by Serve; clients cannot supply it directly.
- Production OPA data contains explicit operators, managed devices, and per-action target allowlists.
- A separate administrator endpoint and PiKVM/Proxmox console can recover the broker VM.
