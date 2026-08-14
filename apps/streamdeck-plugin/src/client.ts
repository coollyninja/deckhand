import { randomUUID } from "node:crypto";

import type { ActionSettings, Plan, StatusValue } from "./contracts.js";

export class DeckhandError extends Error {}

function normalizedBrokerUrl(value: string | undefined): string {
  if (!value) throw new DeckhandError("Broker URL is not configured");
  const url = new URL(value);
  if (url.protocol !== "https:") throw new DeckhandError("Broker URL must use HTTPS");
  url.pathname = url.pathname.replace(/\/$/, "");
  return url.toString().replace(/\/$/, "");
}

async function checkedJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.text()).slice(0, 256);
    throw new DeckhandError(`Broker returned ${response.status}: ${body}`);
  }
  return (await response.json()) as T;
}

export class DeckhandClient {
  async status(brokerUrl: string | undefined, domain: string): Promise<StatusValue> {
    const base = normalizedBrokerUrl(brokerUrl);
    const response = await fetch(`${base}/v1/status/${encodeURIComponent(domain)}`, {
      signal: AbortSignal.timeout(5_000),
    });
    return checkedJson<StatusValue>(response);
  }

  async plan(settings: ActionSettings): Promise<{ plan: Plan; request: Record<string, unknown> }> {
    const request = this.request(settings, null, true);
    const base = normalizedBrokerUrl(settings.brokerUrl);
    const response = await fetch(
      `${base}/v1/actions/${encodeURIComponent(settings.actionId ?? "")}:plan`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: AbortSignal.timeout(10_000),
      },
    );
    return { plan: await checkedJson<Plan>(response), request };
  }

  async execute(
    settings: ActionSettings,
    idempotencyKey: string,
    confirmationToken: string | null,
  ): Promise<Record<string, unknown>> {
    const request = this.request(settings, confirmationToken, false, idempotencyKey);
    const base = normalizedBrokerUrl(settings.brokerUrl);
    const response = await fetch(
      `${base}/v1/actions/${encodeURIComponent(settings.actionId ?? "")}:execute`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: AbortSignal.timeout(15_000),
      },
    );
    return checkedJson<Record<string, unknown>>(response);
  }

  private request(
    settings: ActionSettings,
    confirmationToken: string | null,
    dryRun: boolean,
    idempotencyKey: string = randomUUID(),
  ): Record<string, unknown> {
    if (!settings.actionId || !settings.targetType || !settings.targetId) {
      throw new DeckhandError("Action and target settings are required");
    }
    let parameters: unknown = {};
    if (settings.parameters) parameters = JSON.parse(settings.parameters);
    if (parameters === null || Array.isArray(parameters) || typeof parameters !== "object") {
      throw new DeckhandError("Parameters must be a JSON object");
    }
    return {
      action_id: settings.actionId,
      action_version: settings.actionVersion ?? 1,
      target: { type: settings.targetType, id: settings.targetId },
      parameters,
      context: { client: "streamdeck-plugin" },
      idempotency_key: idempotencyKey,
      dry_run: dryRun,
      confirmation_token: confirmationToken,
    };
  }
}
