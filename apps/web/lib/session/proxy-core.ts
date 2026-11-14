import { createMeraClient } from "@mera/sdk";
import { errorResponse } from "@/lib/api/errors";
import { trustedHeaders } from "./client-ip";
import { REFRESH_RACE_RETRY_MS } from "./constants";
import type { SessionChange } from "./cookies";
import { processRotationStore, refreshSession, rememberedRotation, type RotationStore } from "./refresh";

/*
 * The /api/mera/[...path] BFF proxy, as a pure function of (request, cookies)
 * → (response, session change) so it can be tested with a mocked upstream.
 *
 *   1. Forward to ${MERA_API_URL}/api/v1/<path> with the access cookie as a
 *      bearer token and an allowlist of request headers.
 *   2. On 401 `invalid_token` with a refresh cookie: refresh once (single
 *      flight per process) and retry with the new access token. The new pair
 *      is written back as cookies.
 *   3. Refresh says 409 `refresh_in_progress` (another tab rotated first):
 *      wait ~300 ms, then retry once with whatever pair is now current.
 *   4. Refresh fails: clear the cookies and return 401.
 */

/**
 * Request headers the browser may pass through to the API. Nothing that names
 * the caller's surface is on this list: the browser's `X-Mera-Client` is only
 * the CSRF check (csrf.ts), and the surface is proven by PROXY_SECRET_HEADER,
 * which the BFF adds itself and never takes from the browser.
 */
export const FORWARD_REQUEST_HEADERS = [
  "accept",
  "accept-language",
  "content-type",
  "idempotency-key",
  "if-match",
  "if-none-match",
  "last-event-id",
  "x-request-id",
  // A single-use grant for one high-risk action (P2-M2.5); bound to the user server-side.
  "x-mera-second-factor",
] as const;

/**
 * PROXY_SECRET_HEADER is sent upstream with MERA_WEB_PROXY_SECRET so the API
 * can record audit entries made through the BFF as surface "web" and believe
 * the browser's address (client-ip.ts). A shared secret between the BFF and
 * the API: it never reaches the browser, and a client-supplied value is
 * dropped by the allowlist above.
 */
export { PROXY_SECRET_HEADER } from "./client-ip";

/** Response headers never copied back (hop-by-hop, or invalid once the body is re-streamed). */
const DROP_RESPONSE_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "set-cookie",
  "transfer-encoding",
  "www-authenticate",
]);

const METHODS_WITH_BODY = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export interface ProxyRequest {
  method: string;
  /** Path segments after /api/mera. A leading "api","v1" is accepted and ignored. */
  path: readonly string[];
  /** "?a=b" or "". */
  search: string;
  headers: Headers;
  body?: ArrayBuffer;
  accessToken?: string;
  refreshToken?: string;
}

export interface ProxyDeps {
  apiBaseUrl: string;
  /** MERA_WEB_PROXY_SECRET. Unset: no surface header is sent (the API then records surface "api"). */
  proxySecret?: string;
  /** Upstream transport; tests pass a mock. */
  fetch?: typeof globalThis.fetch;
  sleep?: (ms: number) => Promise<void>;
  store?: RotationStore;
  now?: () => number;
}

export interface ProxyResult {
  response: Response;
  session: SessionChange;
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Normalise the catch-all segments to an /api/v1 path, or null if unacceptable. */
export function upstreamPath(segments: readonly string[]): string | null {
  const rest = segments[0] === "api" && segments[1] === "v1" ? segments.slice(2) : segments;
  if (rest.length === 0) return null;
  // Segments arrive decoded; refuse anything that could climb out of /api/v1.
  if (rest.some((s) => s === "" || s === "." || s === ".." || s.includes("/") || s.includes("\\"))) return null;
  return `/api/v1/${rest.map(encodeURIComponent).join("/")}`;
}

interface Attempt {
  response: Response;
  status: number;
  code?: string;
}

function errorCode(body: unknown): string | undefined {
  const err = (body as { error?: { code?: unknown } } | null)?.error;
  return typeof err?.code === "string" ? err.code : undefined;
}

function copyResponseHeaders(from: Headers): Headers {
  const out = new Headers();
  from.forEach((value, key) => {
    if (!DROP_RESPONSE_HEADERS.has(key.toLowerCase())) out.set(key, value);
  });
  return out;
}

async function send(req: ProxyRequest, url: string, accessToken: string | undefined, deps: ProxyDeps): Promise<Attempt> {
  const client = createMeraClient({ baseUrl: deps.apiBaseUrl, fetch: deps.fetch });
  const headers: Record<string, string> = {};
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value !== null) headers[name] = value;
  }
  Object.assign(headers, trustedHeaders(req.headers, deps.proxySecret));
  if (accessToken) headers.authorization = `Bearer ${accessToken}`;
  const method = req.method.toUpperCase();
  const hasBody = METHODS_WITH_BODY.has(method) && req.body !== undefined && req.body.byteLength > 0;

  let result: { data?: unknown; error?: unknown; response?: Response };
  try {
    result = (await client.request({
      method: method as "GET",
      url: `${url}${req.search}`,
      headers,
      body: hasBody ? req.body : undefined,
      bodySerializer: null,
      parseAs: "stream",
      throwOnError: false,
    })) as typeof result;
  } catch {
    result = {};
  }

  const upstream = result.response;
  if (!upstream) {
    return {
      response: errorResponse(502, "upstream_unavailable", "Can't reach the Mera API right now. Try again in a moment."),
      status: 502,
    };
  }
  const outHeaders = copyResponseHeaders(upstream.headers);
  if (upstream.ok) {
    const body = upstream.status === 204 || upstream.status === 304 ? null : (result.data as ReadableStream | null);
    return { response: new Response(body, { status: upstream.status, headers: outHeaders }), status: upstream.status };
  }
  // The SDK read the error body to parse it; re-serialise what it found.
  const error = result.error;
  const text = typeof error === "string" ? error : JSON.stringify(error ?? {});
  return {
    response: new Response(text, { status: upstream.status, headers: outHeaders }),
    status: upstream.status,
    code: errorCode(error),
  };
}

function signedOut(): Response {
  return errorResponse(401, "invalid_token", "Your session has ended. Sign in again.");
}

export async function proxyToApi(req: ProxyRequest, deps: ProxyDeps): Promise<ProxyResult> {
  const url = upstreamPath(req.path);
  if (!url) return { response: errorResponse(404, "not_found", "Not found."), session: { kind: "keep" } };

  const store = deps.store ?? processRotationStore();
  const now = deps.now ?? Date.now;

  // Another tab may already have rotated our refresh token in this process.
  const current = req.refreshToken ? rememberedRotation(store, req.refreshToken, now()) : undefined;
  if (current) {
    const attempt = await send(req, url, current.access_token, deps);
    return { response: attempt.response, session: { kind: "set", pair: current } };
  }

  const first = await send(req, url, req.accessToken, deps);
  if (first.status !== 401 || first.code !== "invalid_token") return { response: first.response, session: { kind: "keep" } };
  if (!req.refreshToken) {
    return { response: first.response, session: req.accessToken ? { kind: "clear" } : { kind: "keep" } };
  }

  const outcome = await refreshSession(req.refreshToken, {
    ...deps,
    store,
    now,
    upstreamHeaders: trustedHeaders(req.headers, deps.proxySecret),
  });
  switch (outcome.kind) {
    case "ok": {
      const retry = await send(req, url, outcome.pair.access_token, deps);
      return { response: retry.response, session: { kind: "set", pair: outcome.pair } };
    }
    case "in_progress": {
      await (deps.sleep ?? realSleep)(REFRESH_RACE_RETRY_MS);
      const latest = rememberedRotation(store, req.refreshToken, now());
      const retry = await send(req, url, latest?.access_token ?? req.accessToken, deps);
      // The session is alive (someone else just rotated it): never clear here.
      return { response: retry.response, session: latest ? { kind: "set", pair: latest } : { kind: "keep" } };
    }
    case "failed":
      return { response: signedOut(), session: { kind: "clear" } };
    case "unavailable":
      return {
        response: errorResponse(503, "upstream_unavailable", "Can't reach the Mera API right now. Try again in a moment."),
        session: { kind: "keep" },
      };
  }
}
