# Architecture

Deckhand has two intentionally separate planes:

1. The Mac owns the physical Stream Deck and deterministic workstation-local automation.
2. The broker owns authenticated infrastructure intent, authorization, execution, observation, and audit.

Clients submit only versioned action IDs, typed targets, and schema-validated parameters. The broker derives identity from its authenticated ingress, evaluates deny-by-default policy, persists an audit decision before mutation, and dispatches to purpose-scoped adapters. A successful response requires an observed postcondition; a remote timeout after submission is an unknown outcome until reconciliation.

The broker binds to loopback behind Tailscale Serve and an optional management-network mTLS listener. It runs on a dedicated Proxmox VM on a private management VLAN so loss of k3s does not remove the control surface. The broker cannot mutate or recover its own VM. Concrete topology remains in private deployment inventory and is never committed to this public repository.

See the vault implementation plan for the complete component model, phases, controls, and acceptance gates.
