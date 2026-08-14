import {
  action,
  type KeyDownEvent,
  SingletonAction,
  type WillAppearEvent,
} from "@elgato/streamdeck";

import { DeckhandClient } from "../client.js";
import type { StatusSettings } from "../contracts.js";

@action({ UUID: "com.coollyninja.deckhand.status" })
export class StatusAction extends SingletonAction<StatusSettings> {
  private readonly client = new DeckhandClient();

  override async onWillAppear(event: WillAppearEvent<StatusSettings>): Promise<void> {
    await this.refresh(event);
  }

  override async onKeyDown(event: KeyDownEvent<StatusSettings>): Promise<void> {
    await this.refresh(event);
  }

  private async refresh(
    event: WillAppearEvent<StatusSettings> | KeyDownEvent<StatusSettings>,
  ): Promise<void> {
    const { brokerUrl, domain, label = domain } = event.payload.settings;
    if (!domain) {
      await event.action.setTitle("CONFIGURE");
      await event.action.showAlert();
      return;
    }
    try {
      const value = await this.client.status(brokerUrl, domain);
      await event.action.setTitle(`${label}\n${value.state.toUpperCase()}`);
    } catch {
      await event.action.setTitle(`${label}\nOFFLINE`);
      await event.action.showAlert();
    }
  }
}
