import { createMeraClient, refresh as refreshCall } from "@mera/sdk";
import { ROTATION_MEMORY_MS } from "./constants";
import type { TokenPair } from "./cookies";

/*
 * Refresh-token rotation, shared by the /api/mera proxy and proxy.ts.
 *
 * Two tabs (or a page load plus a client fetch) often find the access token
 * expired at the same moment and both present the same refresh token. The
 * backend rotates it for the first and answers the second with 409
 * `refresh_in_progress` (a 20 s grace window; outside it, reuse revokes the
 * whole session). In this process we avoid the 409 entirely:
 *
 *  - single flight: concurrent refreshes of one token share one upstream call;
 *  - rotation memory: for ROTATION_MEMORY_MS after a rotation, presenting the
 *    old refresh token returns the pair it was rotated to.
 *
 * Several web instances behind a load balancer would need this memory shared
 * (e.g. Redis); until then a cross-instance race falls back to the 409 path in
 * proxy-core.ts.
 */

export type RefreshOutcome =
  | { kind: "ok"; pair: TokenPair }
  /** Another request rotated this token moments ago (409 refresh_in_progress). */
  | { kind: "in_progress" }
  /** The refresh token is dead: the session is over. */
  | { kind: "failed"; status: number; body: unknown }
  /** No usable answer (network error, 5xx): keep the session, report the outage. */
  | { kind: "unavailable"; status: number; body: unknown };

export interface RotationStore {
  inflight: Map<string, Promise<RefreshOutcome>>;
  rotated: Map<string, { pair: TokenPair; until: number }>;
}

export function createRotationStore(): RotationStore {
  return { inflight: new Map(), rotated: new Map() };
}

// proxy.ts and the route handlers can be bundled separately; keep one store per process.
const GLOBAL_KEY = Symbol.for("mera.session.rotation");
export function processRotationStore(): RotationStore {
  const g = globalThis as unknown as Record<symbol, RotationStore | undefined>;
  return (g[GLOBAL_KEY] ??= createRotationStore());
}

export function rememberedRotation(store: RotationStore, refreshToken: string, now = Date.now()): TokenPair | undefined {
  for (const [token, entry] of store.rotated) {
    if (entry.until <= now) store.rotated.delete(token);
  }
  return store.rotated.get(refreshToken)?.pair;
}

export interface RefreshDeps {
  apiBaseUrl: string;
  fetch?: typeof globalThis.fetch;
  store?: RotationStore;
  now?: () => number;
  /** Proxy secret and the browser's address (client-ip.ts), so per-IP limits see the browser. */
  upstreamHeaders?: Record<string, string>;
}

function errorCode(body: unknown): string | undefined {
  const err = (body as { error?: { code?: unknown } } | null)?.error;
  return typeof err?.code === "string" ? err.code : undefined;
}

async function callRefresh(refreshToken: string, deps: RefreshDeps): Promise<RefreshOutcome> {
  const client = createMeraClient({ baseUrl: deps.apiBaseUrl, fetch: deps.fetch });
  let result: Awaited<ReturnType<typeof refreshCall>>;
  try {
    result = await refreshCall({ client, headers: deps.upstreamHeaders, body: { refresh_token: refreshToken } });
  } catch (body) {
    return { kind: "unavailable", status: 502, body };
  }
  const { data, error, response } = result;
  if (data && response?.ok) {
    return {
      kind: "ok",
      pair: { access_token: data.access_token, refresh_token: data.refresh_token, expires_in: data.expires_in },
    };
  }
  const status = response?.status ?? 502;
  if (status === 409 && errorCode(error) === "refresh_in_progress") return { kind: "in_progress" };
  if (status >= 400 && status < 500 && status !== 429) return { kind: "failed", status, body: error };
  return { kind: "unavailable", status, body: error };
}

/** Rotate `refreshToken`, at most once per process however many callers ask at once. */
export async function refreshSession(refreshToken: string, deps: RefreshDeps): Promise<RefreshOutcome> {
  const store = deps.store ?? processRotationStore();
  const now = deps.now ?? Date.now;

  const remembered = rememberedRotation(store, refreshToken, now());
  if (remembered) return { kind: "ok", pair: remembered };

  const pending = store.inflight.get(refreshToken);
  if (pending) return pending;

  const attempt = callRefresh(refreshToken, deps)
    .then((outcome) => {
      if (outcome.kind === "ok") {
        store.rotated.set(refreshToken, { pair: outcome.pair, until: now() + ROTATION_MEMORY_MS });
      }
      return outcome;
    })
    .finally(() => store.inflight.delete(refreshToken));
  store.inflight.set(refreshToken, attempt);
  return attempt;
}
