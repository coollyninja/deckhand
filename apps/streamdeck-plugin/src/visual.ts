// The spec §12.3 visual language, rendered as a key image (SVG data URI) so a key
// communicates state at desk distance: color + short title + a staleness/age hint.
import type { Visual } from "./contracts.js";

const COLORS: Record<Visual, string> = {
  healthy: "#1f8a3b", // green
  info: "#1f5fd0", // blue
  ai: "#7a3fd0", // purple
  running: "#0f9fb5", // cyan
  stale: "#c98a12", // amber
  failed: "#c0392b", // red
  offline: "#4a4a4a", // gray
  danger: "#6e0f0f", // dark red
};

function escapeXml(text: string): string {
  return text.replace(/[<>&'"]/g, (c) =>
    c === "<" ? "&lt;" : c === ">" ? "&gt;" : c === "&" ? "&amp;" : c === "'" ? "&apos;" : "&quot;",
  );
}

// Render a 72x72 key: solid state color, a title, and an optional second line.
export function keyImage(visual: Visual, title: string, subtitle = ""): string {
  const bg = COLORS[visual];
  const border = visual === "danger" ? '<rect x="1" y="1" width="70" height="70" fill="none" stroke="#ff5b5b" stroke-width="2"/>' : "";
  const t = escapeXml(title).slice(0, 12);
  const s = escapeXml(subtitle).slice(0, 14);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72">
    <rect width="72" height="72" rx="8" fill="${bg}"/>
    ${border}
    <text x="36" y="34" font-family="Helvetica, Arial, sans-serif" font-size="14" font-weight="600"
          fill="#ffffff" text-anchor="middle">${t}</text>
    <text x="36" y="52" font-family="Helvetica, Arial, sans-serif" font-size="11"
          fill="#e8e8e8" text-anchor="middle">${s}</text>
  </svg>`;
  return `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;
}

// Map a status observation to a visual, honoring the staleness contract the review
// found the plugin ignored: an observation older than stale_after_seconds is amber.
export function statusVisual(
  state: string,
  observedAt: string,
  staleAfterSeconds: number,
): Visual {
  const ageSeconds = (Date.now() - Date.parse(observedAt)) / 1000;
  if (Number.isFinite(ageSeconds) && ageSeconds > staleAfterSeconds) return "stale";
  switch (state) {
    case "healthy":
    case "running":
    case "active":
    case "ok":
      return "healthy";
    case "degraded":
    case "warning":
      return "stale";
    case "failed":
    case "error":
    case "denied":
      return "failed";
    case "unknown":
    case "unavailable":
    case "unconfigured":
      return "offline";
    default:
      return "info";
  }
}
