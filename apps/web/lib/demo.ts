/*
 * DEMO VALUES — no API exists for these yet.
 *
 * Workspaces and the signed-in user are real since Sprint 3 (lib/workspace.ts,
 * GET /workspaces and GET /auth/me). What is left here is stand-in data for
 * features whose endpoints have not landed; replace each with the SDK call
 * when it does.
 */

/**
 * DEMO: rail count badges from mera.js NAV. Live counts arrive with their
 * screens (approvals M6, inbox Phase 2). Connected accounts is live since
 * Sprint 4 (the shell layout passes it to the rail).
 */
export const navCounts: Readonly<Partial<Record<string, number>>> = {
  approvals: 7,
};

/**
 * Feature flags. The inbox (P2-M6) and MCP (P2-M8) are live. A per-workspace
 * flags endpoint does not exist yet; a flag set to false hides a screen from the
 * rail and palette and 404s its route.
 */
export const features = {
  inbox: true, // live since P2-M6
  mcp: true, // live since P2-M8
} as const;

export type Feature = keyof typeof features;
