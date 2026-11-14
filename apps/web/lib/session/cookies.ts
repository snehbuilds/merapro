import { ACCESS_COOKIE_SKEW_SECONDS, REFRESH_COOKIE_MAX_AGE, isProduction } from "./constants";

/*
 * Session cookies. In production they carry the __Host- prefix, which the
 * browser only accepts with Secure, Path=/ and no Domain: the cookie cannot
 * be planted or widened by a sibling subdomain.
 */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  /** Access token lifetime in seconds. */
  expires_in: number;
}

export interface CookieOptions {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
}

export interface CookieWrite {
  name: string;
  value: string;
  options: CookieOptions;
}

/** Anything with a Next-style `cookies.set(name, value, options)`. */
export interface CookieSink {
  set(name: string, value: string, options: CookieOptions): unknown;
}

/** Anything with a Next-style `cookies.get(name)`. */
export interface CookieSource {
  get(name: string): { value: string } | undefined;
}

export function sessionCookieNames(production = isProduction()) {
  const prefix = production ? "__Host-" : "";
  return { access: `${prefix}mera_at`, refresh: `${prefix}mera_rt` } as const;
}

function options(maxAge: number, production: boolean): CookieOptions {
  return { httpOnly: true, secure: production, sameSite: "lax", path: "/", maxAge };
}

export function sessionCookieWrites(pair: TokenPair, production = isProduction()): CookieWrite[] {
  const names = sessionCookieNames(production);
  const accessMaxAge = Math.max(pair.expires_in - ACCESS_COOKIE_SKEW_SECONDS, 1);
  return [
    { name: names.access, value: pair.access_token, options: options(accessMaxAge, production) },
    { name: names.refresh, value: pair.refresh_token, options: options(REFRESH_COOKIE_MAX_AGE, production) },
  ];
}

export function clearedSessionCookieWrites(production = isProduction()): CookieWrite[] {
  const names = sessionCookieNames(production);
  return [
    { name: names.access, value: "", options: options(0, production) },
    { name: names.refresh, value: "", options: options(0, production) },
  ];
}

export function applyCookieWrites(sink: CookieSink, writes: readonly CookieWrite[]): void {
  for (const w of writes) sink.set(w.name, w.value, w.options);
}

export function readSessionTokens(source: CookieSource, production = isProduction()) {
  const names = sessionCookieNames(production);
  return {
    accessToken: source.get(names.access)?.value || undefined,
    refreshToken: source.get(names.refresh)?.value || undefined,
  };
}

/** What a proxied request did to the session, for the caller to write back. */
export type SessionChange = { kind: "keep" } | { kind: "set"; pair: TokenPair } | { kind: "clear" };

export function cookieWritesFor(change: SessionChange, production = isProduction()): CookieWrite[] {
  switch (change.kind) {
    case "keep":
      return [];
    case "set":
      return sessionCookieWrites(change.pair, production);
    case "clear":
      return clearedSessionCookieWrites(production);
  }
}
