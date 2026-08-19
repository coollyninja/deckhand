import { randomUUID } from "node:crypto";

import type { ActionSettings, JobView, Plan, StatusValue } from "./contracts.js";
import { IdentityProvider } from "./identity.js";

// Distinguish the two failure classes the review found were conflated: a request
// the broker refused (DENIED) vs. the broker being unreachable/erroring (OFFLINE).
export class DeckhandDeniedError extends Error {}
export class DeckhandOfflineError extends Error {}

function normalizedBrokerUrl(value: string | undefined): string {
  if (!value) throw new DeckhandOfflineError("Broker URL is not configured");
  // Accept http for the tailnet/loopback broker; the identity token, not TLS, is
  // the auth boundary, and the broker sits behind Tailscale Serve.
  const url = new URL(value);
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new DeckhandOfflineError("Broker URL must be http(s)");
  }
  return url.toString().replace(/\/$/, "");
}

async function checkedJson<T>(response: Response): Promise<T> {
  if (response.status === 401 || response.status === 403) {
    const body = (await response.text()).slice(0, 256);
    throw new DeckhandDeniedError(body || `Broker denied (${response.status})`);
  }
  if (!response.ok) {
    const body = (await response.text()).slice(0, 256);
    throw new DeckhandOfflineError(`Broker returned ${response.status}: ${body}`);
  }
  return (await response.json()) as T;
}

export class DeckhandClient {
  private readonly identity: IdentityProvider;

  constructor(identityUrl?: string) {
    this.identity = new IdentityProvider(identityUrl);
  }

  private async headers(): Promise<Record<string, string>> {
    return {
      "Content-Type": "application/json",
      "X-Deckhand-Identity": await this.identity.token(),
    };
  }

  private async fetchJson<T>(url: string, init: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetch(url, init);
    } catch (cause) {
      throw new DeckhandOfflineError(`Broker unreachable: ${String(cause)}`);
    }
    return checkedJson<T>(response);
  }

  async status(brokerUrl: string | undefined, domain: string): Promise<StatusValue> {
    const base = normalizedBrokerUrl(brokerUrl);
    return this.fetchJson<StatusValue>(`${base}/v1/status/${encodeURIComponent(domain)}`, {
      headers: await this.headers(),
      signal: AbortSignal.timeout(5_000),
    });
  }

  async plan(settings: ActionSettings): Promise<{ plan: Plan; idempotencyKey: string }> {
    const idempotencyKey = randomUUID();
    const request = this.request(settings, null, true, idempotencyKey);
    const base = normalizedBrokerUrl(settings.brokerUrl);
    const plan = await this.fetchJson<Plan>(
      `${base}/v1/actions/${encodeURIComponent(settings.actionId ?? "")}:plan`,
      {
        method: "POST",
        headers: await this.headers(),
        body: JSON.stringify(request),
        signal: AbortSignal.timeout(10_000),
      },
    );
    return { plan, idempotencyKey };
  }

  async execute(
    settings: ActionSettings,
    idempotencyKey: string,
    confirmationToken: string | null,
  ): Promise<JobView> {
    // The SAME idempotency key is reused across retries so a lost response does not
    // create a duplicate job (the review found a fresh key was minted each retry).
    const request = this.request(settings, confirmationToken, false, idempotencyKey);
    const base = normalizedBrokerUrl(settings.brokerUrl);
    return this.fetchJson<JobView>(
      `${base}/v1/actions/${encodeURIComponent(settings.actionId ?? "")}:execute`,
      {
        method: "POST",
        headers: await this.headers(),
        body: JSON.stringify(request),
        signal: AbortSignal.timeout(15_000),
      },
    );
  }

  async job(brokerUrl: string | undefined, jobId: string): Promise<JobView> {
    const base = normalizedBrokerUrl(brokerUrl);
    return this.fetchJson<JobView>(`${base}/v1/jobs/${encodeURIComponent(jobId)}`, {
      headers: await this.headers(),
      signal: AbortSignal.timeout(5_000),
    });
  }

  private request(
    settings: ActionSettings,
    confirmationToken: string | null,
    dryRun: boolean,
    idempotencyKey: string,
  ): Record<string, unknown> {
    if (!settings.actionId || !settings.targetType || !settings.targetId) {
      throw new DeckhandDeniedError("Action and target settings are required");
    }
    let parameters: unknown = {};
    if (settings.parameters) parameters = JSON.parse(settings.parameters);
    if (parameters === null || Array.isArray(parameters) || typeof parameters !== "object") {
      throw new DeckhandDeniedError("Parameters must be a JSON object");
    }
    return {
      action_id: settings.actionId,
      action_version: settings.actionVersion ?? 1,
      target: { type: settings.targetType, id: settings.targetId },
      parameters,
      context: { client: "streamdeck-plugin", control: "streamdeck" },
      idempotency_key: idempotencyKey,
      dry_run: dryRun,
      confirmation_token: confirmationToken,
    };
  }
}
