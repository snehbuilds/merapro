/*
 * API key scopes (docs/04 §2.5). The API validates them (422 invalid_scopes);
 * this list only drives the picker. Grouped by resource for display.
 */
export const API_KEY_SCOPE_GROUPS: readonly { label: string; scopes: readonly string[] }[] = [
  { label: "Sources", scopes: ["sources:read", "sources:write"] },
  { label: "Content", scopes: ["content:read"] },
  { label: "Accounts", scopes: ["accounts:read", "accounts:write"] },
  { label: "Groups", scopes: ["groups:read", "groups:write"] },
  { label: "Drafts & media", scopes: ["drafts:read", "drafts:write", "media:write"] },
  { label: "Approvals", scopes: ["approvals:read", "approvals:decide"] },
  { label: "Publishing", scopes: ["publishing:read", "publishing:write"] },
  { label: "Engagement", scopes: ["engagement:read", "engagement:write"] },
  { label: "Analytics", scopes: ["analytics:read"] },
  { label: "Automation & agents", scopes: ["automation:write", "agents:read", "agents:write"] },
  { label: "Administration", scopes: ["admin"] },
];

export const API_KEY_SCOPES: readonly string[] = API_KEY_SCOPE_GROUPS.flatMap((g) => g.scopes);

export const API_KEY_EXPIRY_OPTIONS: readonly { label: string; days: number | null }[] = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
  { label: "Never", days: null },
];

export type ApiKeyState = "active" | "revoked" | "expired";

export function apiKeyState(key: { revoked_at: string | null; expires_at: string | null }, now: number = Date.now()): ApiKeyState {
  if (key.revoked_at) return "revoked";
  if (key.expires_at && Date.parse(key.expires_at) <= now) return "expired";
  return "active";
}
