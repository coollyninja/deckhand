# Deployment

Production deployment targets a dedicated minimal VM with one VLAN-291 NIC, rootless application containers, hardened systemd units, Tailscale Serve as primary ingress, Caddy mTLS as management-network fallback, systemd credentials, egress allowlists, and independent Proxmox/PiKVM recovery.

`systemd/deckhand-plugin@.service` is the fail-closed base for isolated plugin sidecars. Provision one static `deckhand-<plugin-id>` account per enabled sidecar, install its signed self-contained executable as `/opt/deckhand/plugins/<plugin-id>/current`, and keep its configuration at `/etc/deckhand/plugins/<plugin-id>.yaml`. The private site overlay supplies `<plugin-id>.runtime` with `DECKHAND_BROKER_UID`, `LoadCredential=` drop-ins, and reviewed `IPAddressAllow=` CIDRs. The public unit intentionally starts with `IPAddressDeny=any` and contains no topology.

Deployment assets remain non-operational until the environment inventory and protected-resource baseline are approved.
