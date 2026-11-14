import { createMeraClient, oauthExchange, stepUp } from "@mera/sdk";
import { NextResponse, type NextRequest } from "next/server";
import { trustedHeaders } from "./client-ip";
import { mfaChallenge, setMfaCookie } from "./mfa";
import { apiBaseUrl, isProduction } from "./constants";
import { applyCookieWrites, readSessionTokens, sessionCookieWrites, type CookieOptions } from "./cookies";
import { DEFAULT_AFTER_LOGIN, safeNextPath } from "./next-path";

/*
 * Google / Microsoft sign-in with PKCE (docs/04 §2.2, M2.7):
 *
 *   /api/session/oauth/<provider>/start
 *     → verifier in a short-lived httpOnly cookie
 *     → 302 to the API's /auth/oauth/<provider>/authorize with the S256 challenge
 *   provider → API → /api/session/oauth/callback?code=…  (or ?error=…)
 *     → POST /auth/oauth/exchange {code, code_verifier} server-side
 *     → session cookies, verifier cookie cleared, into the app.
 *
 * The verifier cookie is what binds the callback to the browser that started
 * the flow: a code injected from elsewhere cannot be exchanged without it.
 * It is SameSite=Lax, not Strict, because it must ride the cross-site
 * redirect back from the provider.
 *
 * Step-up (P3-M7.3): /start?step_up=1 runs the same flow for someone already signed in
 * who has no password. The callback hops (same-origin) to /api/session/oauth/step-up,
 * where the Strict session cookie is present again; the API trades the code for a
 * single-use proof, which goes back to the page in the URL fragment (never sent to a
 * server, so never in a log).
 */

export const OAUTH_PROVIDERS = ["google", "microsoft"] as const;
export type OAuthProvider = (typeof OAUTH_PROVIDERS)[number];

export function isOAuthProvider(value: string): value is OAuthProvider {
  return (OAUTH_PROVIDERS as readonly string[]).includes(value);
}

const VERIFIER_MAX_AGE = 10 * 60;

export function verifierCookieName(production = isProduction()): string {
  return `${production ? "__Host-" : ""}mera_oauth`;
}

function verifierCookieOptions(maxAge: number, production = isProduction()): Omit<CookieOptions, "sameSite"> & { sameSite: "lax" } {
  return { httpOnly: true, secure: production, sameSite: "lax", path: "/", maxAge };
}

function base64url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** RFC 7636: 32 random bytes → 43-char verifier; challenge = BASE64URL(SHA-256(verifier)). */
export async function createPkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  return { verifier, challenge: await pkceChallenge(verifier) };
}

export async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

/** Where the browser is sent to reach the API (MERA_API_URL is often a private hostname). */
export function publicApiUrl(): string {
  return (process.env.MERA_PUBLIC_API_URL ?? apiBaseUrl()).replace(/\/+$/, "");
}

/** This app's public origin, for the redirect_uri the API sends the browser back to. */
export function publicAppUrl(request: NextRequest): string {
  return (process.env.MERA_PUBLIC_URL ?? request.nextUrl.origin).replace(/\/+$/, "");
}

export const OAUTH_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  provider_denied: "Sign-in was cancelled.",
  provider_error: "The sign-in provider had a problem. Try again, or sign in with your password.",
  account_exists: "An account with this email exists — sign in with your password.",
};

export function oauthErrorMessage(code: string): string {
  return OAUTH_ERROR_MESSAGES[code] ?? "We couldn't sign you in with that provider. Try again, or sign in with your password.";
}

function loginWithError(request: NextRequest, code: string, next: string): NextResponse {
  const url = new URL("/login", request.url);
  url.searchParams.set("oauth_error", /^[a-z_]{1,64}$/.test(code) ? code : "oauth_failed");
  if (next !== DEFAULT_AFTER_LOGIN) url.searchParams.set("next", next);
  const res = NextResponse.redirect(url);
  res.cookies.set(verifierCookieName(), "", verifierCookieOptions(0));
  return res;
}

export async function handleOAuthStart(request: NextRequest, provider: string): Promise<NextResponse> {
  if (!isOAuthProvider(provider)) return NextResponse.redirect(new URL("/login", request.url));
  const next = safeNextPath(request.nextUrl.searchParams.get("next"));
  const isStepUp = request.nextUrl.searchParams.get("step_up") === "1";
  const { verifier, challenge } = await createPkcePair();

  const authorize = new URL(`${publicApiUrl()}/api/v1/auth/oauth/${provider}/authorize`);
  authorize.searchParams.set("redirect_uri", `${publicAppUrl(request)}/api/session/oauth/callback`);
  authorize.searchParams.set("code_challenge", challenge);
  authorize.searchParams.set("code_challenge_method", "S256");

  const res = NextResponse.redirect(authorize);
  res.cookies.set(
    verifierCookieName(),
    JSON.stringify(isStepUp ? { v: verifier, next, s: 1 } : { v: verifier, next }),
    verifierCookieOptions(VERIFIER_MAX_AGE),
  );
  return res;
}

function readVerifier(request: NextRequest): { verifier: string; next: string; stepUp: boolean } | null {
  const raw = request.cookies.get(verifierCookieName())?.value;
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { v?: unknown; next?: unknown; s?: unknown };
    if (typeof parsed.v !== "string" || parsed.v.length < 43) return null;
    return {
      verifier: parsed.v,
      next: safeNextPath(typeof parsed.next === "string" ? parsed.next : undefined),
      stepUp: parsed.s === 1,
    };
  } catch {
    return null;
  }
}

/**
 * The session cookies are SameSite=Strict, and this response is the end of a
 * cross-site redirect chain, so a 302 into the app would arrive without them
 * and bounce to /login. A same-origin HTML hop makes the next navigation
 * same-site.
 */
function continueTo(path: string): NextResponse {
  const href = path.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  const html = `<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=${href}"><title>Signing in · Mera</title><p><a href="${href}">Continue to Mera</a></p>`;
  return new NextResponse(html, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "referrer-policy": "no-referrer" },
  });
}

export async function handleOAuthCallback(
  request: NextRequest,
  deps: { apiBaseUrl?: string; fetch?: typeof globalThis.fetch; proxySecret?: string } = {},
): Promise<NextResponse> {
  const stored = readVerifier(request);
  const next = stored?.next ?? DEFAULT_AFTER_LOGIN;
  const params = request.nextUrl.searchParams;
  const error = params.get("error");
  if (error) return loginWithError(request, error, next);

  const code = params.get("code");
  if (!code || !stored) return loginWithError(request, "oauth_failed", next);
  if (stored.stepUp) {
    // The session cookie is Strict and absent on this cross-site redirect: hop first.
    return continueTo(`/api/session/oauth/step-up?code=${encodeURIComponent(code)}`);
  }

  const client = createMeraClient({ baseUrl: deps.apiBaseUrl ?? apiBaseUrl(), fetch: deps.fetch });
  const headers = trustedHeaders(request.headers, deps.proxySecret ?? (process.env.MERA_WEB_PROXY_SECRET || undefined));
  const result = await oauthExchange({ client, headers, body: { code, code_verifier: stored.verifier } }).catch(() => null);
  if (!result?.data || !result.response?.ok) {
    const challenge = mfaChallenge(result?.error);
    if (challenge) {
      // A second step is owed (P2-M2.3): finish on the login page with the code.
      const url = new URL("/login", request.url);
      url.searchParams.set("mfa", "1");
      if (next !== DEFAULT_AFTER_LOGIN) url.searchParams.set("next", next);
      const res = NextResponse.redirect(url);
      res.cookies.set(verifierCookieName(), "", verifierCookieOptions(0));
      setMfaCookie(res, challenge);
      return res;
    }
    const apiCode = (result?.error as { error?: { code?: unknown } } | undefined)?.error?.code;
    return loginWithError(request, typeof apiCode === "string" ? apiCode : "oauth_failed", next);
  }

  const res = continueTo(next);
  applyCookieWrites(res.cookies, sessionCookieWrites(result.data));
  res.cookies.set(verifierCookieName(), "", verifierCookieOptions(0));
  return res;
}

/** The same-origin half of step-up: the session is present again; trade the code. */
export async function handleStepUp(
  request: NextRequest,
  deps: { apiBaseUrl?: string; fetch?: typeof globalThis.fetch; proxySecret?: string } = {},
): Promise<NextResponse> {
  const stored = readVerifier(request);
  const next = stored?.next ?? DEFAULT_AFTER_LOGIN;
  const code = request.nextUrl.searchParams.get("code");
  const { accessToken } = readSessionTokens(request.cookies);
  let fragment = "step_up_error=1";
  if (stored?.stepUp && code && accessToken) {
    const client = createMeraClient({ baseUrl: deps.apiBaseUrl ?? apiBaseUrl(), accessToken, fetch: deps.fetch });
    const headers = trustedHeaders(request.headers, deps.proxySecret ?? (process.env.MERA_WEB_PROXY_SECRET || undefined));
    const result = await stepUp({ client, headers, body: { code, code_verifier: stored.verifier } }).catch(() => null);
    if (result?.data && result.response?.ok) fragment = `step_up=${encodeURIComponent(result.data.proof)}`;
  }
  const res = NextResponse.redirect(new URL(`${next}#${fragment}`, request.url));
  res.cookies.set(verifierCookieName(), "", verifierCookieOptions(0));
  return res;
}
