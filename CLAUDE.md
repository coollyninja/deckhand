# Deckhand core

Inherit `../CLAUDE.md` and the vault standards. This repository is the topology-neutral core and canonical source for plugin, lock, pack, API, policy, and client contracts.

- Do not add vendor- or site-specific endpoints, target IDs, credentials, or action catalogs to core.
- Built-ins pass through the same validation and ownership rules as external plugins.
- External plugin loading stays explicit and fail-closed.
- Mutation paths require durable audit, policy, idempotency, confirmation where declared, and observed postconditions.
- Run Python, policy, Stream Deck, container, contract, and public-surface gates in proportion to the change.
