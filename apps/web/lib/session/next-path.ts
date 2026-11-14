/*
 * The `next` parameter on /login: where to go after signing in. Only a
 * same-origin relative path is honoured, so /login?next=https://evil.example
 * (or //evil.example, /\evil.example, javascript:…) cannot turn the login
 * page into an open redirect.
 */

const PROBE_ORIGIN = "http://mera.invalid";

export const DEFAULT_AFTER_LOGIN = "/";

export function safeNextPath(raw: string | null | undefined, fallback: string = DEFAULT_AFTER_LOGIN): string {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 2048) return fallback;
  // Must be an absolute path on this origin: "/x", never "//host" or "/\host".
  if (!raw.startsWith("/") || raw.startsWith("//")) return fallback;
  // Browsers treat "\" as "/" in URLs; control characters and whitespace can
  // smuggle a scheme or host past naive checks.
  if (/[\\\u0000-\u001f\u007f\s]/.test(raw)) return fallback;
  let url: URL;
  try {
    url = new URL(raw, PROBE_ORIGIN);
  } catch {
    return fallback;
  }
  if (url.origin !== PROBE_ORIGIN) return fallback;
  // Never bounce back into the auth pages or the BFF.
  if (/^\/(login|register|api)(\/|$)/.test(url.pathname)) return fallback;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** /login?next=<the current path>, for redirecting an unauthenticated request. */
export function loginHref(next?: string | null): string {
  const safe = next ? safeNextPath(next, "") : "";
  return safe && safe !== DEFAULT_AFTER_LOGIN ? `/login?next=${encodeURIComponent(safe)}` : "/login";
}
