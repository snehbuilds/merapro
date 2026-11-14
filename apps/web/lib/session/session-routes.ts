import { createMeraClient, login, logout, register } from "@mera/sdk";
import { NextResponse, type NextRequest } from "next/server";
import { errorResponse } from "@/lib/api/errors";
import { apiBaseUrl } from "./constants";
import { trustedHeaders } from "./client-ip";
import { applyCookieWrites, clearedSessionCookieWrites, readSessionTokens, sessionCookieWrites } from "./cookies";
import { csrfRejection } from "./csrf";
import { mfaChallenge, mfaRequiredResponse } from "./mfa";

/*
 * app/api/session/{login,register,logout}: the only places tokens are minted
 * into cookies. Upstream error envelopes pass through unchanged (status and
 * body) so forms can show `field_errors` inline.
 */

export interface SessionRouteDeps {
  apiBaseUrl?: string;
  fetch?: typeof globalThis.fetch;
  /** MERA_WEB_PROXY_SECRET; defaults to the environment. */
  proxySecret?: string;
}

function upstreamHeaders(request: Request, deps: SessionRouteDeps): Record<string, string> {
  return trustedHeaders(request.headers, deps.proxySecret ?? (process.env.MERA_WEB_PROXY_SECRET || undefined));
}

type Json = Record<string, unknown>;

async function readJson(request: Request): Promise<Json | null> {
  try {
    const body: unknown = await request.json();
    return body && typeof body === "object" && !Array.isArray(body) ? (body as Json) : null;
  } catch {
    return null;
  }
}

function str(body: Json, key: string): string {
  const v = body[key];
  return typeof v === "string" ? v : "";
}

function upstreamError(error: unknown, response: Response | undefined): Response {
  if (!response) return errorResponse(502, "upstream_unavailable", "Can't reach the Mera API right now. Try again in a moment.");
  const out = NextResponse.json(error ?? {}, { status: response.status });
  const retryAfter = response.headers.get("retry-after");
  if (retryAfter) out.headers.set("retry-after", retryAfter);
  return out;
}

function client(deps: SessionRouteDeps) {
  return createMeraClient({ baseUrl: deps.apiBaseUrl ?? apiBaseUrl(), fetch: deps.fetch });
}

function deviceLabel(request: Request): string {
  const ua = request.headers.get("user-agent") ?? "";
  return `Web · ${ua}`.slice(0, 120);
}

async function signIn(request: Request, email: string, password: string, deps: SessionRouteDeps) {
  return login({
    client: client(deps),
    headers: upstreamHeaders(request, deps),
    body: { email, password, device_label: deviceLabel(request) },
  }).catch(() => ({
    data: undefined,
    error: undefined,
    response: undefined,
  }));
}

export async function handleLogin(request: Request, deps: SessionRouteDeps = {}): Promise<Response> {
  const rejected = csrfRejection(request);
  if (rejected) return rejected;
  const body = await readJson(request);
  if (!body) return errorResponse(400, "bad_request", "Send email and password as JSON.");

  const { data, error, response } = await signIn(request, str(body, "email"), str(body, "password"), deps);
  const challenge = mfaChallenge(error);
  if (challenge) return mfaRequiredResponse(challenge);
  if (!data || !response?.ok) return upstreamError(error, response);

  const out = new NextResponse(null, { status: 204 });
  applyCookieWrites(out.cookies, sessionCookieWrites(data));
  return out;
}

export async function handleRegister(request: Request, deps: SessionRouteDeps = {}): Promise<Response> {
  const rejected = csrfRejection(request);
  if (rejected) return rejected;
  const body = await readJson(request);
  if (!body) return errorResponse(400, "bad_request", "Send email, password and display_name as JSON.");

  const email = str(body, "email");
  const password = str(body, "password");
  const created = await register({
    client: client(deps),
    headers: upstreamHeaders(request, deps),
    body: { email, password, display_name: str(body, "display_name") },
  }).catch(() => ({ data: undefined, error: undefined, response: undefined }));
  if (!created.data || !created.response?.ok) return upstreamError(created.error, created.response);

  // Registration returns the user, not tokens: sign in to start the session.
  const signedIn = await signIn(request, email, password, deps);
  const out = NextResponse.json(created.data, { status: 201 });
  if (signedIn.data && signedIn.response?.ok) applyCookieWrites(out.cookies, sessionCookieWrites(signedIn.data));
  return out;
}

export async function handleLogout(request: NextRequest, deps: SessionRouteDeps = {}): Promise<Response> {
  const rejected = csrfRejection(request);
  if (rejected) return rejected;
  const { refreshToken } = readSessionTokens(request.cookies);
  if (refreshToken) {
    // Revoke server-side; the cookies are cleared whatever the API says.
    await logout({ client: client(deps), body: { refresh_token: refreshToken } }).catch(() => undefined);
  }
  const out = new NextResponse(null, { status: 204 });
  applyCookieWrites(out.cookies, clearedSessionCookieWrites());
  return out;
}
