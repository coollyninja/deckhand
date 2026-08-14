# Architecture

Deckhand has two intentionally separate planes:

1. The Mac owns the physical Stream Deck and deterministic workstation-local automation.
2. The broker owns authenticated infrastructure intent, authorization, execution, observation, and audit.

Clients submit only versioned action IDs, typed targets, and schema-validated parameters. The broker derives identity from its authenticated ingress, evaluates deny-by-default policy, persists an audit decision before mutation, and dispatches to purpose-scoped adapters. A successful response requires an observed postcondition; a remote timeout after submission is an unknown outcome until reconciliation.

The broker binds to loopback behind Tailscale Serve, a local Caddy identity boundary, and an optional management-network mTLS listener. Caddy authenticates its hop with a process-specific assertion. Tailnet device authority comes from a Serve-injected application capability; a client-supplied device header cannot grant permission. Deployment topology is supplied by a private site overlay and never committed to this public repository.

The core has no built-in service domains or site targets. A version-locked plugin manager loads explicitly enabled `dh-*` integrations and merges their adapters, actions, and status providers after validating each manifest and configuration schema. Bundled plugins use the same loading path as installed plugins. Catalog and policy references use logical target aliases; the private site overlay binds those aliases to actual systems.

See the vault implementation plan for the complete component model, phases, controls, and acceptance gates.
