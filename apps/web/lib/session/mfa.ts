import { createMeraClient, verifyMfa } from "@mera/sdk";
import { NextResponse, type NextRequest } from "next/server";
import { errorResponse } from "@/lib/api/errors";
import { trustedHeaders } from "./client-ip";
import { apiBaseUrl, isProduction } from "./constants";
import { applyCookieWrites, sessionCookieWrites } from "./cookies";
import { csrfRejection } from "./csrf";

/*
 * Two-step sign-in in the BFF (P2-M2.3). When the API answers a sign-in with
 * `mfa_required`, its challenge token goes into a short-lived httpOnly cookie,
 * never to the page's JavaScript; the login form then posts only the code to
 * /api/session/mfa, which redeems the challenge and sets the session cookies.
 */

const MAX_AGE = 5 * 60;

export function mfaCookieName(production = isProduction()): string {
  return `${production ? "__Host-" : ""}mera_mfa`;
}

export function setMfaCookie(res: NextResponse, token: string): void {
  res.cookies.set(mfaCookieName(), token, {
    httpOnly: true,
    secure: isProduction(),
    sameSite: "strict",
    path: "/",
    maxAge: MAX_AGE,
  });
}

function clearMfaCookie(res: NextResponse): void {
  res.cookies.set(mfaCookieName(), "", { httpOnly: true, secure: isProduction(), sameSite: "strict", path: "/", maxAge: 0 });
}

/** The API's `mfa_required` error carries the challenge in `error.detail`. */
export function mfaChallenge(error: unknown): string | null {
  const err = (error as { error?: { code?: unknown; detail?: unknown } } | null)?.error;
  return err?.code === "mfa_required" && typeof err.detail === "string" ? err.detail : null;
}

/** What the browser is told: a code is owed. The token itself stays in the cookie. */
export function mfaRequiredResponse(token: string): NextResponse {
  const res = NextResponse.json(
    { error: { code: "mfa_required", message: "Enter the code from your authenticator app.", field_errors: [] } },
    { status: 401 },
  );
  setMfaCookie(res, token);
  return res;
}

export async function handleMfa(
  request: NextRequest,
  deps: { apiBaseUrl?: string; fetch?: typeof globalThis.fetch; proxySecret?: string } = {},
): Promise<Response> {
  const rejected = csrfRejection(request);
  if (rejected) return rejected;
  const token = request.cookies.get(mfaCookieName())?.value;
  if (!token) return errorResponse(401, "invalid_mfa_challenge", "That sign-in has expired. Start again.");
  let code = "";
  try {
    const body = (await request.json()) as { code?: unknown };
    code = typeof body.code === "string" ? body.code : "";
  } catch {
    return errorResponse(400, "bad_request", "Send the code as JSON.");
  }
  const client = createMeraClient({ baseUrl: deps.apiBaseUrl ?? apiBaseUrl(), fetch: deps.fetch });
  const headers = trustedHeaders(request.headers, deps.proxySecret ?? (process.env.MERA_WEB_PROXY_SECRET || undefined));
  const result = await verifyMfa({ client, headers, body: { mfa_token: token, code } }).catch(() => null);
  if (!result?.response) return errorResponse(502, "upstream_unavailable", "Can't reach the Mera API right now. Try again in a moment.");
  if (!result.data || !result.response.ok) {
    const out = NextResponse.json(result.error ?? {}, { status: result.response.status });
    const errCode = (result.error as { error?: { code?: unknown } } | undefined)?.error?.code;
    if (errCode === "invalid_mfa_challenge") clearMfaCookie(out);
    return out;
  }
  const out = new NextResponse(null, { status: 204 });
  applyCookieWrites(out.cookies, sessionCookieWrites(result.data));
  clearMfaCookie(out);
  return out;
}
