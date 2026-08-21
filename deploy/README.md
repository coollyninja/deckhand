# Deployment

Production deployment targets a dedicated minimal VM with one VLAN-291 NIC, rootless application containers, hardened systemd units, Tailscale Serve as primary ingress, Caddy mTLS as management-network fallback, systemd credentials, egress allowlists, and independent Proxmox/PiKVM recovery.

`systemd/deckhand-wasm-host@.service` is the fail-closed base for the out-of-process WASM host (`deckhand-wasm-host`), which serves one signed WASM capability over the peer-authenticated Unix-socket host transport. Provision one static `deckhand-<plugin-id>` account per enabled host, install its signed `.wasm` component as `/opt/deckhand/plugins/<plugin-id>/component.wasm`, and its self-contained host executable as `/opt/deckhand/plugins/<plugin-id>/current`. The private site overlay supplies `<plugin-id>.runtime` with `DECKHAND_BROKER_UID`, `DECKHAND_WASM_ROBOT`, `DECKHAND_WASM_CAPABILITY`, `LoadCredential=` drop-ins, and reviewed `IPAddressAllow=` CIDRs. The public unit intentionally starts with `IPAddressDeny=any` and contains no topology.

Deployment assets remain non-operational until the environment inventory and protected-resource baseline are approved.
