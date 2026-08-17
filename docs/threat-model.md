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

## Non-negotiable assumptions

- Caddy and Tailscale Serve run on the same host as the loopback broker.
- The proxy assertion is readable only by the proxy and broker services.
- App capability data is authored in Tailnet grants and injected by Serve; clients cannot supply it directly.
- Production OPA data contains explicit operators, managed devices, and per-action target allowlists.
- A separate administrator endpoint and PiKVM/Proxmox console can recover the broker VM.
