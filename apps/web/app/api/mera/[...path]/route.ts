import { NextResponse, type NextRequest } from "next/server";
import { apiBaseUrl } from "@/lib/session/constants";
import { applyCookieWrites, cookieWritesFor, readSessionTokens } from "@/lib/session/cookies";
import { csrfRejection } from "@/lib/session/csrf";
import { proxyToApi } from "@/lib/session/proxy-core";

/*
 * BFF proxy: /api/mera/<path> → ${MERA_API_URL}/api/v1/<path> with the access
 * cookie as the bearer token. The logic (refresh, retry, races) is in
 * lib/session/proxy-core.ts.
 */

async function handle(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  try {
    const rejected = csrfRejection(request);
    if (rejected) return rejected;

    const { accessToken, refreshToken } = readSessionTokens(request.cookies);
    const method = request.method.toUpperCase();
    // Bound the body before buffering it: the API refuses oversized uploads too, but this
    // process would otherwise hold the whole thing in memory first (review S7-M5).
    const limit = isMediaUpload(method, path) ? MAX_UPLOAD_BODY : MAX_BODY;
    const declared = Number(request.headers.get("content-length") ?? "0");
    if (!Number.isFinite(declared) || declared > limit) return tooLarge();
    const body = method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();
    if (body && body.byteLength > limit) return tooLarge();

    const { response, session } = await proxyToApi(
      { method, path, search: request.nextUrl.search, headers: request.headers, body, accessToken, refreshToken },
      { apiBaseUrl: apiBaseUrl(), proxySecret: process.env.MERA_WEB_PROXY_SECRET || undefined },
    );

    const out = new NextResponse(response.body, { status: response.status, headers: response.headers });
    out.headers.set("cache-control", "no-store");
    applyCookieWrites(out.cookies, cookieWritesFor(session));
    return out;
  } catch (error) {
    // Never log headers, cookies, the request body, or the query string: any can carry secrets.
    console.error("web.bff_proxy_failed", {
      message: "The web server could not proxy a request to the API.",
      method: request.method,
      path: `/${path.join("/")}`,
      requestId: request.headers.get("x-request-id") ?? undefined,
      errorType: error instanceof Error ? error.name : typeof error,
      stack: error instanceof Error ? error.stack : undefined,
    });
    return NextResponse.json(
      { error: { code: "service_unavailable", message: "The service is temporarily unavailable.", field_errors: [] } },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
}

const MAX_BODY = 2 * 1024 * 1024;
const MAX_UPLOAD_BODY = 20 * 1024 * 1024 + 256 * 1024;

function isMediaUpload(method: string, path: string[]): boolean {
  return method === "POST" && path.length === 3 && path[0] === "workspaces" && path[2] === "media";
}

function tooLarge(): Response {
  return NextResponse.json(
    { error: { code: "file_too_large", message: "The upload is too large. Images can be at most 20 MB.", field_errors: [] } },
    { status: 413, headers: { "cache-control": "no-store" } },
  );
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
