import { action, type KeyDownEvent, SingletonAction } from "@elgato/streamdeck";

import { DeckhandClient, DeckhandDeniedError } from "../client.js";
import type { ActionSettings, Confirmation } from "../contracts.js";
import { keyImage } from "../visual.js";

type Pending = {
  challenge: Confirmation;
  idempotencyKey: string;
  armedAt: number;
};

// A minimum arming delay between the plan press and the confirm press, so an
// accidental double-tap cannot satisfy a confirmation in one gesture (the review
// found the original executed on any second press with no delay).
const ARM_DELAY_MS = 600;
// A confirmation not completed within this window is dropped.
const PENDING_TTL_MS = 30_000;

@action({ UUID: "com.coollyninja.deckhand.action" })
export class TypedAction extends SingletonAction<ActionSettings> {
  private readonly pending = new Map<string, Pending>();

  private key(s: ActionSettings): string {
    return `${s.actionId ?? ""}:${s.targetType ?? ""}:${s.targetId ?? ""}`;
  }

  override async onKeyDown(event: KeyDownEvent<ActionSettings>): Promise<void> {
    const s = event.payload.settings;
    const key = this.key(s);
    const client = new DeckhandClient(s.identityUrl);

    try {
      const existing = this.pending.get(key);
      if (existing) {
        const age = Date.now() - existing.armedAt;
        if (age > PENDING_TTL_MS) {
          this.pending.delete(key);
        } else if (age < ARM_DELAY_MS) {
          // Too fast to be deliberate — treat as an accidental double-tap and
          // hold the confirmation rather than executing.
          await event.action.setImage(keyImage("danger", "HOLD", "confirm"));
          return;
        } else {
          const job = await client.execute(s, existing.idempotencyKey, existing.challenge.token);
          this.pending.delete(key);
          await event.action.setImage(keyImage("running", job.state.toUpperCase(), s.targetId));
          return;
        }
      }

      // First press: plan (dry-run), surface any confirmation.
      const { plan, idempotencyKey } = await client.plan(s);
      if (!plan.executable) {
        await event.action.setImage(keyImage("failed", "DENIED", plan.denial_reason ?? ""));
        await event.action.showAlert();
        return;
      }
      if (plan.confirmation) {
        if (plan.confirmation.mode !== "confirm") {
          await event.action.setImage(keyImage("failed", "UNSUP", plan.confirmation.mode));
          await event.action.showAlert();
          return;
        }
        this.pending.set(key, {
          challenge: plan.confirmation,
          idempotencyKey,
          armedAt: Date.now(),
        });
        await event.action.setImage(keyImage("danger", "CONFIRM?", s.targetId));
        return;
      }
      // No confirmation required — execute directly.
      const job = await client.execute(s, idempotencyKey, null);
      await event.action.setImage(keyImage("running", job.state.toUpperCase(), s.targetId));
    } catch (cause) {
      this.pending.delete(key);
      // Classify: a broker refusal is DENIED; anything else (unreachable, 5xx) is
      // OFFLINE — the review found these were conflated so a lost response looked
      // like a denial and invited a duplicate retry.
      const denied = cause instanceof DeckhandDeniedError;
      await event.action.setImage(keyImage(denied ? "failed" : "offline", denied ? "DENIED" : "OFFLINE"));
      await event.action.showAlert();
    }
  }
}
