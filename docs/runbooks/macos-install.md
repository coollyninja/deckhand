# macOS and Stream Deck installation runbook

1. Install the signed Deckhand release into a dedicated virtual environment.
2. Copy `config/macos.example.yaml` to a private configuration path and replace every alias with an authoritative bundle ID, URL, or Shortcut name.
3. Generate a random local agent token, store the authoritative value in Keychain, and provision the restricted runtime token file for launchd.
4. Substitute absolute paths in the launchd template, install it as a user agent, and verify the service binds only to `127.0.0.1`.
5. Grant only required macOS permissions. Record Accessibility, Automation, Screen Recording, Microphone, and Camera status; do not grant unused permissions.
6. Install the signed `.streamDeckPlugin`; configure broker URL, action ID, target alias, and parameters through property inspectors. Do not paste credentials.
7. Install the global profile and Smart Profiles. Confirm every page has Home/Back and every broker tile shows offline when the broker is stopped.
8. Test app, browser, display/workspace, recording, and meeting paths against observed postconditions.

To remove: unload launchd, uninstall the plugin/profile, remove the runtime token, revoke the Mac client certificate/Tailnet capability, and retain logs only according to the operator's policy.

