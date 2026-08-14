# Deckhand macOS agent

The macOS agent exposes only cataloged, alias-based workstation actions over loopback. It never accepts caller-supplied shell, AppleScript, bundle IDs, URLs, paths, or key sequences.

Create a private configuration from `config/macos.example.yaml`, store a random bearer value in the configured token file, and run `deckhand-macos-agent`. Production packaging uses launchd and macOS Keychain-backed provisioning; the token file is a restricted runtime bridge, not a repository secret.
