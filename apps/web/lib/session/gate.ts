import { NextResponse, type NextRequest } from "next/server";
import { trustedHeaders } from "./client-ip";
import { apiBaseUrl } from "./constants";
import { applyCookieWrites, clearedSessionCookieWrites, readSessionTokens, sessionCookieNames, sessionCookieWrites } from "./cookies";
import { loginHref } from "./next-path";
import { refreshSession, type RefreshDeps } from "./refresh";

/*
 * The page gate (run from proxy.ts) for workspace pages:
 *
 *  - no session cookies at all → redirect to /login?next=<this path>;
 *  - refresh cookie but no access cookie (it expired) → refresh here, because
 *    server components can read cookies but cannot write them. The new pair
 *    is set on the response and on the forwarded request, so this render
 *    already sees it;
 *  - refresh fails → clear cookies, redirect to login.
 *
 * This is the optimistic check (docs: "Proxy is not a full authorization
 * solution"): the API still authorises every call.
 */
export async function gateRequest(request: NextRequest, deps: Partial<RefreshDeps> = {}): Promise<NextResponse> {
  const { accessToken, refreshToken } = readSessionTokens(request.cookies);
  if (accessToken) return NextResponse.next();

  const toLogin = () =>
    NextResponse.redirect(new URL(loginHref(`${request.nextUrl.pathname}${request.nextUrl.search}`), request.url));

  if (!refreshToken) return toLogin();

  const upstreamHeaders = trustedHeaders(request.headers, process.env.MERA_WEB_PROXY_SECRET || undefined);
  const outcome = await refreshSession(refreshToken, { apiBaseUrl: apiBaseUrl(), upstreamHeaders, ...deps });
  if (outcome.kind === "ok") {
    const names = sessionCookieNames();
    request.cookies.set(names.access, outcome.pair.access_token);
    request.cookies.set(names.refresh, outcome.pair.refresh_token);
    const headers = new Headers(request.headers);
    headers.set("cookie", request.cookies.toString());
    const res = NextResponse.next({ request: { headers } });
    applyCookieWrites(res.cookies, sessionCookieWrites(outcome.pair));
    return res;
  }
  if (outcome.kind === "failed") {
    const res = toLogin();
    applyCookieWrites(res.cookies, clearedSessionCookieWrites());
    return res;
  }
  // in_progress (another instance rotated it) or unavailable: let the page
  // render; it redirects to login itself if the API still says no.
  return NextResponse.next();
}
