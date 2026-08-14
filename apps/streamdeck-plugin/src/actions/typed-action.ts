import { action, type KeyDownEvent, SingletonAction } from "@elgato/streamdeck";

import { DeckhandClient } from "../client.js";
import type { ActionSettings, Confirmation } from "../contracts.js";

type Pending = {
  challenge: Confirmation;
  idempotencyKey: string;
};

@action({ UUID: "com.coollyninja.deckhand.action" })
export class TypedAction extends SingletonAction<ActionSettings> {
  private readonly client = new DeckhandClient();
  private readonly pending = new Map<string, Pending>();

  override async onKeyDown(event: KeyDownEvent<ActionSettings>): Promise<void> {
    const settings = event.payload.settings;
    const key = `${settings.actionId ?? ""}:${settings.targetType ?? ""}:${settings.targetId ?? ""}`;
    try {
      const existing = this.pending.get(key);
      if (existing && Date.parse(existing.challenge.expires_at) > Date.now()) {
        await this.client.execute(settings, existing.idempotencyKey, existing.challenge.token);
        this.pending.delete(key);
        await event.action.setTitle("REQUESTED");
        return;
      }
      this.pending.delete(key);
      const { plan, request } = await this.client.plan(settings);
      if (!plan.executable) throw new Error(plan.denial_reason ?? "Request denied");
      if (plan.confirmation) {
        if (plan.confirmation.mode !== "confirm") {
          throw new Error(`Unsupported confirmation mode: ${plan.confirmation.mode}`);
        }
        this.pending.set(key, {
          challenge: plan.confirmation,
          idempotencyKey: String(request.idempotency_key),
        });
        await event.action.setTitle(`CONFIRM\n${settings.targetId ?? ""}`);
        return;
      }
      await this.client.execute(settings, String(request.idempotency_key), null);
      await event.action.setTitle("REQUESTED");
    } catch {
      this.pending.delete(key);
      await event.action.setTitle("DENIED");
      await event.action.showAlert();
    }
  }
}
