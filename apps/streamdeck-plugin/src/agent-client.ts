// Client for the LOCAL macOS agent. Mac-local keys (mic, focus, displays, OBS)
// talk to the agent directly rather than through the broker, so they keep working
// even when the broker is offline (spec §20.3). The agent authenticates with a
// static local bearer token read from a file the plugin's property inspector
// never sees the value of — here we accept the token via settings for the loopback
// case, since the agent binds to 127.0.0.1 only.

import type { LocalResult } from "./contracts.js";

export class AgentError extends Error {}

const DEFAULT_AGENT = "http://127.0.0.1:19471";

export class AgentClient {
  constructor(
    private readonly agentUrl: string = DEFAULT_AGENT,
    private readonly token: string | undefined = undefined,
  ) {}

  async execute(actionId: string, target: string): Promise<LocalResult> {
    let response: Response;
    try {
      response = await fetch(`${this.agentUrl}/v1/actions:execute`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        },
        body: JSON.stringify({ action_id: actionId, target }),
        signal: AbortSignal.timeout(8_000),
      });
    } catch (cause) {
      throw new AgentError(`macOS agent unreachable: ${String(cause)}`);
    }
    if (!response.ok) {
      const body = (await response.text()).slice(0, 256);
      throw new AgentError(`agent returned ${response.status}: ${body}`);
    }
    return (await response.json()) as LocalResult;
  }
}
