# Stream Deck bring-up runbook

How to go from "everything is built" to pressing keys that do things. This is the
last mile — the physical install and wiring on your Mac.

There are three local services on the Mac and one remote broker:

```
Stream Deck plugin ──(bearer)──▶ macOS agent  (localhost:19471)  ← Mac-local keys
        │
        └──(signed token from)──▶ identity issuer (localhost:19472)
        │
        └──(X-Deckhand-Identity)─▶ broker (pve0 LXC 103, via Tailscale)  ← lab keys
```

Mac-local keys (mic, focus, displays, OBS) need only the **agent**. Lab keys need
the **issuer** + **broker**.

---

## 1. Generate the local key material

The issuer needs an Ed25519 signing key; the broker already trusts a public key
(the one deployed to LXC 103). To use the SAME identity end-to-end, generate a
fresh pair and put the public half on the broker, or reuse the broker's existing
`identity.pub.pem`. Simplest for a first run — generate a new pair and register it:

```bash
mkdir -p ~/.deckhand
openssl genpkey -algorithm ed25519 -out ~/.deckhand/identity.key
openssl pkey -in ~/.deckhand/identity.key -pubout -out ~/.deckhand/identity.pub.pem
# a local bearer for the agent + issuer (defence in depth on loopback)
openssl rand -hex 32 > ~/.deckhand/agent-token
```

Register this public key with the broker (replace the one currently on LXC 103,
or add it): copy `~/.deckhand/identity.pub.pem` to the broker's
`/etc/deckhand/secrets/identity.pub.pem` and restart the broker container.

## 2. Fill in the macOS inventory

Copy the template and fill in your real facts (bundle ids, focus/display Shortcut
names, audio device names, OBS settings):

```bash
cp config/macos.example.yaml ~/.deckhand/macos.yaml
$EDITOR ~/.deckhand/macos.yaml
```

For the focus/pomodoro/display modes: create matching **Shortcuts** in the
Shortcuts app (e.g. a "Set Work Focus" shortcut with a Set Focus action) and put
their exact names in the inventory. For audio device switching:
`brew install switchaudio-osx`. For OBS: Tools → WebSocket Server Settings →
enable, note the port + password, put the password in a file and point
`obs.password_file` at it.

## 3. Start the three local services

Three terminals (or launchd plists — see deploy/macos):

```bash
# macOS agent
DECKHAND_MACOS_INVENTORY_PATH=~/.deckhand/macos.yaml \
DECKHAND_MACOS_TOKEN_FILE=~/.deckhand/agent-token \
  uv run deckhand-macos-agent

# identity issuer (subject/device must match what the broker's policy expects)
DECKHAND_ISSUER_SIGNING_KEY_FILE=~/.deckhand/identity.key \
DECKHAND_ISSUER_SUBJECT=bobby \
DECKHAND_ISSUER_DEVICE=macbook-air-m2 \
DECKHAND_ISSUER_CHANNEL=mgmt-mtls \
  uv run deckhand-issuer
```

The broker is already running on pve0 (LXC 103). Reach it over Tailscale — the
broker URL is `http://<lxc-103-tailnet-name>:19470` once the LXC is on your
tailnet, or via an SSH port-forward for a first test:
`ssh -L 19470:<broker-lxc-ip>:19470 root@<pve0>`.

## 4. Install the plugin

Double-click the packaged plugin, or install via the CLI:

```bash
open dist/com.coollyninja.deckhand.streamDeckPlugin
# or: streamdeck install dist/com.coollyninja.deckhand.streamDeckPlugin
```

The Stream Deck app will show a **Deckhand** category with three actions:
**Status**, **Action**, **Local**.

## 5. Lay out the §12.1 home page

Drag keys onto the deck and configure each via its property inspector:

| Position | Action type | Config |
|---|---|---|
| Mic (bottom-left) | Local | actionId `mac.mic.toggle`, label `MIC`, State=ON when `live` |
| Focus | Local | actionId `mac.focus.set`, target `work`, label `FOCUS` |
| Displays | Local | actionId `mac.display.mode_apply`, target `office` |
| Pomodoro | Local | actionId `mac.pomodoro.start`, target `deep` |
| Record | Local | actionId `mac.obs.record_start`, label `REC`, State=ON when `recording` |
| Lab health | Status | brokerUrl + identityUrl `http://127.0.0.1:19472/token`, domain `<a status alias>` |
| Alerts / Tailnet | Status | as above with the relevant status domain |
| (VM power, later) | Action | brokerUrl + identityUrl, actionId `proxmox.vm.ensure_running`, targetType `proxmox_vm`, targetId `<alias>` |

Set **Agent URL** on Local keys to `http://127.0.0.1:19471` and **Identity issuer
URL** on Status/Action keys to `http://127.0.0.1:19472/token`.

## 6. Press keys

- **Local keys** work immediately against the agent (mic toggles, focus sets,
  etc.) — even with the broker offline.
- **Status keys** show live broker state, refreshing every 15s, amber when stale.
- **Action keys** (mutation, once the lab plugins are activated) plan → show a
  dark-red CONFIRM? → a second press after the 600ms arming delay executes.

## Troubleshooting

- Key shows OFFLINE (gray): the backend is unreachable. For Local keys, the agent
  isn't running; for Status/Action, the issuer or broker is unreachable.
- Key shows DENIED (red): the broker refused — check policy/inventory (the subject/
  device the issuer mints must be in the broker's OPA operator/device allowlists).
- Status keys need the read-only lab plugins configured + activated on the broker
  (dh-proxmox/dh-prometheus/dh-tailscale) — that's the remaining lab-side step.
```
