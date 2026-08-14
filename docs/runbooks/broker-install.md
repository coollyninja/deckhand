# Broker installation runbook

1. Provision a minimal current Debian/Ubuntu VM from private inventory with one management-VLAN NIC, encrypted-capable disks, NTP, console access, and independent backup.
2. Create `deckhand`, `deckhand-worker`, and `deckhand-opa` system users with no login shell; create shared group `deckhand` and restricted `/var/lib/deckhand`.
3. Install the signed release under `/opt/deckhand`; place catalog, policy, and public configuration under `/etc/deckhand` owned by root.
4. Resolve secret references into per-service files. Create a random proxy assertion readable only by Caddy and the broker.
5. Install OPA, broker, worker, and scheduler units. Run `systemd-analyze security` against each unit and record justified findings.
6. Configure Caddy and Tailscale Serve to the loopback Caddy listener. Funnel and public tunnels must remain disabled.
7. Configure management mTLS with a dedicated client CA. Verify an invalid/untrusted certificate cannot reach the broker.
8. Start OPA, broker, worker, scheduler, Caddy, and Tailscale in dependency order. Check `/healthz`, `/readyz`, `/metrics`, and logs.
9. Load read-only inventory and credentials first. Verify every domain reports healthy, degraded, unavailable, or unconfigured—never silently absent.
10. Run the acceptance matrix in read-only mode. Mutation activation is a separate per-action approval and credential step.

Rollback: stop broker/worker/scheduler, restore the prior immutable release and policy/catalog bundle, restore the online SQLite backup only if required, and rerun readiness plus audit-chain verification before re-enabling clients.

