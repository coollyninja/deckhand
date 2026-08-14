# Deployment

Production deployment targets a dedicated minimal VM with one VLAN-291 NIC, rootless application containers, hardened systemd units, Tailscale Serve as primary ingress, Caddy mTLS as management-network fallback, systemd credentials, egress allowlists, and independent Proxmox/PiKVM recovery.

Deployment assets remain non-operational until the environment inventory and protected-resource baseline are approved.

