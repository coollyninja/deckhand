import {
  action,
  type DidReceiveSettingsEvent,
  type KeyDownEvent,
  SingletonAction,
  type WillAppearEvent,
  type WillDisappearEvent,
} from "@elgato/streamdeck";

import { DeckhandClient } from "../client.js";
import type { StatusSettings } from "../contracts.js";
import { keyImage, statusVisual } from "../visual.js";

const REFRESH_MS = 15_000;

// A broker status tile. Refreshes on a timer (the review found the original only
// fetched on appear/keydown, so hours-old state showed as current) and honors the
// staleness contract via statusVisual.
@action({ UUID: "com.coollyninja.deckhand.status" })
export class StatusAction extends SingletonAction<StatusSettings> {
  private readonly timers = new Map<string, ReturnType<typeof setInterval>>();

  override async onWillAppear(event: WillAppearEvent<StatusSettings>): Promise<void> {
    await this.refresh(event.action, event.payload.settings);
    this.startTimer(event.action, event.payload.settings);
  }

  override onWillDisappear(event: WillDisappearEvent<StatusSettings>): void {
    const timer = this.timers.get(event.action.id);
    if (timer) clearInterval(timer);
    this.timers.delete(event.action.id);
  }

  override async onDidReceiveSettings(
    event: DidReceiveSettingsEvent<StatusSettings>,
  ): Promise<void> {
    await this.refresh(event.action, event.payload.settings);
    this.startTimer(event.action, event.payload.settings);
  }

  override async onKeyDown(event: KeyDownEvent<StatusSettings>): Promise<void> {
    await this.refresh(event.action, event.payload.settings);
  }

  private startTimer(action: WillAppearEvent<StatusSettings>["action"], settings: StatusSettings) {
    const existing = this.timers.get(action.id);
    if (existing) clearInterval(existing);
    this.timers.set(
      action.id,
      setInterval(() => void this.refresh(action, settings), REFRESH_MS),
    );
  }

  private async refresh(
    action: WillAppearEvent<StatusSettings>["action"],
    settings: StatusSettings,
  ): Promise<void> {
    const { brokerUrl, identityUrl, domain, label } = settings;
    if (!domain) {
      await action.setImage(keyImage("offline", "CONFIG"));
      return;
    }
    const client = new DeckhandClient(identityUrl);
    try {
      const value = await client.status(brokerUrl, domain);
      const visual = statusVisual(value.state, value.observed_at, value.stale_after_seconds);
      await action.setImage(keyImage(visual, label ?? domain, value.state));
    } catch {
      await action.setImage(keyImage("offline", label ?? domain, "OFFLINE"));
    }
  }
}
