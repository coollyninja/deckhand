# Deckhand Stream Deck plugin

The official-SDK TypeScript plugin provides status and typed-action keys. It stores only broker location, catalog identifiers, target aliases, parameters, and presentation preferences. Infrastructure credentials never enter Stream Deck settings.

The plugin plans before execution. When the broker returns a confirmation challenge, the first press displays `CONFIRM`; a second deliberate press within the challenge lifetime submits the exact bound token. Reconnect and refresh paths fetch authoritative state and never replay a press.
