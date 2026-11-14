import { createMeraClient } from "@mera/sdk";
import { BROWSER_CLIENT_HEADERS, PROXY_BASE } from "@/lib/session/constants";
import { toApiError, type ApiError } from "./errors";

/*
 * Browser-side transport. Client components call SDK operations with
 * `{ client: browserApi }`: requests go to /api/mera (the BFF), which holds
 * the tokens. The X-Mera-Client header is the CSRF check.
 */
export const browserApi = createMeraClient({ baseUrl: PROXY_BASE, headers: { ...BROWSER_CLIENT_HEADERS } });

/** Same-origin client for the BFF's own /api/session routes (not part of the API spec). */
const sessionTransport = createMeraClient({ baseUrl: "", headers: { ...BROWSER_CLIENT_HEADERS } });

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

interface SdkResult<T> {
  data?: T;
  error?: unknown;
  response?: Response;
}

/** Turn an SDK call into ok/data or ok:false/ApiError (never throws). */
export async function settle<T>(call: Promise<SdkResult<T>>): Promise<Result<T>> {
  try {
    const { data, error, response } = await call;
    if (response?.ok) return { ok: true, data: data as T };
    return { ok: false, error: toApiError(error, response?.status ?? 0, response?.headers) };
  } catch (thrown) {
    return { ok: false, error: toApiError(thrown, 0) };
  }
}

/** A fresh Idempotency-Key for one create action (docs/04 §3.4). */
export function idempotencyHeaders(): { "Idempotency-Key": string } {
  return { "Idempotency-Key": crypto.randomUUID() };
}

export function sessionLogin(body: { email: string; password: string }) {
  return settle(sessionTransport.post<{ 204: unknown }, unknown, false>({ url: "/api/session/login", body }));
}

/** The second step of signing in (P2-M2.3); the challenge travels in an httpOnly cookie. */
export function sessionMfa(body: { code: string }) {
  return settle(sessionTransport.post<{ 204: unknown }, unknown, false>({ url: "/api/session/mfa", body }));
}

export function sessionRegister(body: { email: string; password: string; display_name: string }) {
  return settle(sessionTransport.post<{ 201: unknown }, unknown, false>({ url: "/api/session/register", body }));
}

export function sessionLogout() {
  return settle(sessionTransport.post<{ 204: unknown }, unknown, false>({ url: "/api/session/logout" }));
}
