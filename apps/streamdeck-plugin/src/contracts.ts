export type ActionSettings = {
  brokerUrl?: string;
  actionId?: string;
  actionVersion?: number;
  targetType?: string;
  targetId?: string;
  parameters?: string;
};

export type StatusSettings = {
  brokerUrl?: string;
  domain?: string;
  label?: string;
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
  executable: boolean;
  required_confirmation: Confirmation["mode"];
  confirmation: Confirmation | null;
  denial_reason: string | null;
};

export type StatusValue = {
  state: string;
  observed_at: string;
  stale_after_seconds: number;
  details: Record<string, unknown>;
};
