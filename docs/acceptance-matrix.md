# Acceptance matrix

This matrix translates the private source specification into public, topology-neutral evidence. Live rows remain blocked until private inventory is approved.

| ID | Requirement | Automated evidence | Live/manual evidence |
|---|---|---|---|
| F-01 | Global status is authoritative or explicitly stale/unconfigured | status aggregation tests | compare deck tiles with source APIs |
| F-02 | Reconnect never replays mutation | plugin state-machine review | disconnect/reconnect with instrumented target |
| F-03 | Local Mac actions survive broker outage | macOS-agent API tests | stop broker; execute app/workspace actions |
| F-04 | Allowlisted VM power action verifies actual state | catalog/policy/worker tests | approved test guest start/shutdown |
| S-01 | No arbitrary action, field, command, URL, or target | schema and alias tests | profile/settings inspection |
| S-02 | Direct/spoofed identity headers cannot authenticate | ingress tests | LAN and Tailnet direct probes |
| S-03 | Mutation fails when OPA or audit is unavailable | policy/store tests | service-stop drills |
| S-04 | Confirmation is exact, expiring, device-bound, single-use | store tests | deliberate second-press UX test |
| S-05 | Purpose credentials cannot escape target scope | OPA tests | target API permission probes |
| R-01 | Restart does not duplicate mutation | idempotency/lease tests | kill worker after submit and reconcile |
| R-02 | Timeout becomes unknown until observed | lease/reconciler tests | induced target response loss |
| R-03 | k3s loss does not remove broker | deployment topology review | controlled k3s outage |
| R-04 | Tailnet loss leaves mTLS fallback | proxy config review | disable Tailnet and connect from MGMT |
| R-05 | Database/VM/plugin restore works | backup tooling tests | scheduled restore drill |
| U-01 | Key labels/states are readable and deterministic | manifest validation | normal desk-position review |
| U-02 | Dangerous controls are visually isolated | profile review | operator walkthrough |
| U-03 | Failures identify target, class, and diagnostic path | API/plugin tests | injected adapter failure |

