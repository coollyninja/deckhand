import {
  action,
  type KeyDownEvent,
  SingletonAction,
  type WillAppearEvent,
} from "@elgato/streamdeck";

import { AgentClient, AgentError } from "../agent-client.js";
import type { LocalSettings } from "../contracts.js";
import { keyImage } from "../visual.js";

// A Mac-local key: presses drive the localhost macOS agent (mic, focus, displays,
// OBS, sleep-inhibit, etc.). These keep working when the broker is offline. For
// stateful actions the key reflects the returned state (on=green, off=gray).
@action({ UUID: "com.coollyninja.deckhand.local" })
export class LocalAction extends SingletonAction<LocalSettings> {
  override async onWillAppear(event: WillAppearEvent<LocalSettings>): Promise<void> {
    const { label } = event.payload.settings;
    await event.action.setImage(keyImage("info", label ?? "LOCAL"));
  }

  override async onKeyDown(event: KeyDownEvent<LocalSettings>): Promise<void> {
    const s = event.payload.settings;
    if (!s.actionId) {
      await event.action.setImage(keyImage("offline", "CONFIG"));
      await event.action.showAlert();
      return;
    }
    const client = new AgentClient(s.agentUrl);
    try {
      const result = await client.execute(s.actionId, s.target ?? "_");
      const active = s.stateOn ? result.state === s.stateOn : result.verified;
      await event.action.setImage(
        keyImage(active ? "healthy" : "offline", s.label ?? s.actionId, result.state),
      );
    } catch (cause) {
      const offline = cause instanceof AgentError;
      await event.action.setImage(keyImage(offline ? "offline" : "failed", s.label ?? "ERR"));
      await event.action.showAlert();
    }
  }
}
