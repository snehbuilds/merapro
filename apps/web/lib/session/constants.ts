/*
 * The browser session (docs/12 F4, Sprint 3 security decision):
 *
 *  - Access and refresh tokens live only in httpOnly, SameSite=Strict cookies
 *    set by the Next.js BFF (app/api/session/*). Browser JS never sees them.
 *  - Client components call the API through /api/mera/*, which adds the
 *    bearer token, refreshes once on `invalid_token`, and rewrites cookies.
 *  - CSRF: every non-GET request to /api/mera and /api/session must carry
 *    `X-Mera-Client: web` (a custom header a cross-site form cannot send
 *    without a CORS preflight we never grant). SameSite=Strict is layer two.
 */

export const CLIENT_HEADER = "x-mera-client";
export const CLIENT_HEADER_VALUE = "web";

/** Headers every browser-side API call sends. */
export const BROWSER_CLIENT_HEADERS: Readonly<Record<string, string>> = { "X-Mera-Client": CLIENT_HEADER_VALUE };

/** Browser-side base URL of the BFF proxy (app/api/mera/[...path]). */
export const PROXY_BASE = "/api/mera";

/** Server-side base URL of the API. Compose sets MERA_API_URL=http://api:8000. */
export function apiBaseUrl(): string {
  const fallback = process.env.NODE_ENV === "production" ? "https://api.drtea.cloud" : "http://localhost:8000";
  return (process.env.MERA_API_URL ?? process.env.MERA_PUBLIC_API_URL ?? fallback).replace(/\/+$/, "");
}

export function isProduction(): boolean {
  return process.env.NODE_ENV === "production";
}

/** Refresh tokens live 30 days (docs/04 §2.1); the backend enforces the real expiry. */
export const REFRESH_COOKIE_MAX_AGE = 30 * 24 * 60 * 60;

/** Drop the access cookie a little before the token expires so we refresh first. */
export const ACCESS_COOKIE_SKEW_SECONDS = 30;

/** How long to wait before retrying after the backend says `refresh_in_progress`. */
export const REFRESH_RACE_RETRY_MS = 300;

/**
 * How long a rotated pair is remembered for the old refresh token, so a second
 * tab presenting that token gets the new pair instead of a 409. Matches the
 * backend's 20 s reuse grace window.
 */
export const ROTATION_MEMORY_MS = 20_000;
