/*
 * The browser's address, passed to the API on every call the BFF makes for it
 * (P2-M0.9). Without this the API sees the web server as the client for every
 * user, so per-IP limits such as the login lockout would be shared by all of
 * them.
 *
 * The address is the right-most X-Forwarded-For entry: the one added by the
 * reverse proxy in front of the web app, or by Next itself (from the socket)
 * when there is none. Entries further left are whatever the client claimed.
 * The API believes X-Mera-Client-IP only alongside the proxy secret, so the
 * header is sent only when a secret is configured.
 */

export const PROXY_SECRET_HEADER = "x-mera-proxy-secret";
export const CLIENT_IP_HEADER = "x-mera-client-ip";

const ADDRESS = /^[0-9A-Fa-f:.]{2,45}$/;

export function clientIp(headers: Headers): string | null {
  const forwarded = headers.get("x-forwarded-for");
  if (!forwarded) return null;
  const last = forwarded
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .at(-1);
  if (!last) return null;
  // "::ffff:203.0.113.9" from a dual-stack socket is still one address.
  return ADDRESS.test(last) ? last : null;
}

/** Headers proving the call comes from the BFF, with the browser's address. */
export function trustedHeaders(headers: Headers, proxySecret: string | undefined): Record<string, string> {
  if (!proxySecret) return {};
  const out: Record<string, string> = { [PROXY_SECRET_HEADER]: proxySecret };
  const ip = clientIp(headers);
  if (ip) out[CLIENT_IP_HEADER] = ip;
  return out;
}
