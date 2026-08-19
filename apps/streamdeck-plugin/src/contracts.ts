// Wire contracts shared between the plugin and its two backends:
//  - the broker (infrastructure: status, typed intent, confirmation, jobs)
//  - the local macOS agent (workstation actions, reachable even when the broker is down)

export type ActionSettings = {
  // Broker action settings (infrastructure keys)
  brokerUrl?: string;
  identityUrl?: string; // local identity-issuer that mints signed tokens
  actionId?: string;
  actionVersion?: number;
  targetType?: string;
  targetId?: string;
  parameters?: string;
};

export type StatusSettings = {
  brokerUrl?: string;
  identityUrl?: string;
  domain?: string;
  label?: string;
};

// A local macOS-agent key: talks to the agent directly (bearer), not the broker.
export type LocalSettings = {
  agentUrl?: string; // default http://127.0.0.1:19471
  actionId?: string; // e.g. mac.mic.toggle
  target?: string; // alias, or "_" for whole-machine actions
  label?: string;
  // For status-style local keys, the state field to read back and how to color it.
  stateOn?: string; // state value treated as "active/on" (green)
  stateOff?: string; // state value treated as "inactive/off" (gray)
};

export type Confirmation = {
  id: string;
  token: string;
  mode: "none" | "confirm" | "hold" | "typed" | "dual_control" | "policy";
  expires_at: string;
  prompt: string;
};

export type Plan = {
  request_digest: string;
  confirmation_digest: string;
  executable: boolean;
  required_confirmation: Confirmation["mode"];
  confirmation: Confirmation | null;
  denial_reason: string | null;
};

export type JobView = {
  id: string;
  state: string;
  action_id: string;
  result: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
};

export type StatusValue = {
  state: string;
  observed_at: string;
  stale_after_seconds: number;
  details: Record<string, unknown>;
};

export type LocalResult = {
  action_id: string;
  target: string;
  state: string;
  verified: boolean;
  details: Record<string, unknown>;
};

// The visual state vocabulary from spec §12.3. Drives key color/glyph.
export type Visual =
  | "healthy" // green
  | "info" // blue
  | "ai" // purple
  | "running" // cyan
  | "stale" // amber
  | "failed" // red
  | "offline" // gray
  | "danger"; // dark red
