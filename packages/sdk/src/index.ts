/*
 * @mera/sdk: the generated client (src/gen, do not edit) plus one hand-written
 * factory. Every operation in src/gen takes `{ client }` so callers pick the
 * transport per call:
 *
 *   const api = createMeraClient({ baseUrl: "/api/mera", headers: { "X-Mera-Client": "web" } });
 *   const { data, error } = await listWorkspaces({ client: api });
 */
import { createClient, type Client } from "./gen/client";

export * from "./gen";
export type { Client as MeraClient } from "./gen/client";

/** The envelope every non-2xx API response carries (docs/04 §3.1). */
export type { ErrorEnvelope as MeraErrorEnvelope, FieldError as MeraFieldError } from "./gen";

export interface CreateMeraClientOptions {
  /**
   * Origin (and optional prefix) the generated `/api/v1/...` paths are
   * appended to: `http://api:8000` server-side, `/api/mera` in the browser.
   */
  baseUrl: string;
  /** Transport. Defaults to the global fetch; tests pass a mock. */
  fetch?: typeof globalThis.fetch;
  /** Headers sent with every request. */
  headers?: Record<string, string>;
  /**
   * Bearer token for operations the spec marks as secured. A function is
   * called per request.
   */
  accessToken?: string | (() => string | undefined | Promise<string | undefined>);
}

export function createMeraClient({ baseUrl, fetch, headers, accessToken }: CreateMeraClientOptions): Client {
  return createClient({
    baseUrl: baseUrl.replace(/\/+$/, ""),
    ...(fetch ? { fetch } : {}),
    ...(headers ? { headers } : {}),
    ...(accessToken !== undefined ? { auth: typeof accessToken === "function" ? () => accessToken() : accessToken } : {}),
  });
}
