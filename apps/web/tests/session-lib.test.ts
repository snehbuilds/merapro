import { createHash } from "node:crypto";
import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";
import { displayMessage, fieldErrorMap, formErrors, parseRetryAfter, toApiError } from "@/lib/api/errors";
import { apiKeyState } from "@/lib/api-keys";
import { loginHref, safeNextPath } from "@/lib/session/next-path";
import { handleOAuthCallback, handleOAuthStart, pkceChallenge } from "@/lib/session/oauth";
import { initials, slugify, SLUG_PATTERN } from "@/lib/workspace";

describe("safeNextPath (the login `next` parameter)", () => {
  it.each([
    ["/w/acme/team", "/w/acme/team"],
    ["/w/acme/approvals?filter=pending#top", "/w/acme/approvals?filter=pending#top"],
    ["/invite/abc123", "/invite/abc123"],
    ["/w/acme/../acme/team", "/w/acme/team"],
  ])("keeps same-origin path %s", (raw, expected) => {
    expect(safeNextPath(raw)).toBe(expected);
  });

  it.each([
    "https://evil.example/w/acme",
    "//evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/%0a/evil.example".replace("%0a", "\n"),
    "javascript:alert(1)",
    "w/acme",
    " /w/acme",
    "/login",
    "/login?next=/w/acme",
    "/register",
    "/api/session/logout",
    "",
    "/" + "a".repeat(3000),
  ])("rejects %j", (raw) => {
    expect(safeNextPath(raw)).toBe("/");
  });

  it("handles missing values and custom fallbacks", () => {
    expect(safeNextPath(undefined)).toBe("/");
    expect(safeNextPath(null, "/welcome")).toBe("/welcome");
  });

  it("builds /login?next= only for a safe, non-default path", () => {
    expect(loginHref("/w/acme/team?x=1")).toBe("/login?next=%2Fw%2Facme%2Fteam%3Fx%3D1");
    expect(loginHref("/")).toBe("/login");
    expect(loginHref("https://evil.example")).toBe("/login");
    expect(loginHref(undefined)).toBe("/login");
  });
});

describe("error envelope mapping", () => {
  const envelope = {
    error: {
      code: "password_breached",
      message: "This password appears in known breaches. Choose another.",
      detail: null,
      field_errors: [
        { field: "password", code: "password_breached", message: "This password appears in known breaches. Choose another." },
        { field: "password", code: "second", message: "ignored: first error per field wins" },
        { field: "address.city", code: "missing", message: "City is required." },
      ],
      request_id: "req-9",
      docs_url: "https://docs.mera.app/errors/password_breached",
    },
  };

  it("maps field_errors to one message and code per field", () => {
    expect(fieldErrorMap(envelope.error.field_errors)).toEqual({
      messages: {
        password: "This password appears in known breaches. Choose another.",
        "address.city": "City is required.",
      },
      codes: { password: "password_breached", "address.city": "missing" },
    });
    expect(fieldErrorMap(undefined)).toEqual({ messages: {}, codes: {} });
  });

  it("normalises an envelope to an ApiError", () => {
    const err = toApiError(envelope, 422);
    expect(err).toMatchObject({ status: 422, code: "password_breached", requestId: "req-9" });
    expect(err.fieldErrors.password).toMatch(/breaches/);
  });

  it("normalises non-envelopes and network failures", () => {
    expect(toApiError("Bad Gateway", 502)).toMatchObject({ status: 502, code: "unknown_error" });
    expect(toApiError(new TypeError("fetch failed"), 0)).toMatchObject({ status: 0, code: "network_error" });
  });

  it("splits errors between inline fields and a form-level notice, never dropping one", () => {
    const err = toApiError(envelope, 422);
    expect(formErrors(err, ["email", "password"] as const)).toEqual({
      fields: { password: "This password appears in known breaches. Choose another." },
      form: ["City is required."],
    });
    const noFields = toApiError({ error: { code: "invalid_credentials", message: "Wrong email or password." } }, 401);
    expect(formErrors(noFields, ["email", "password"] as const)).toEqual({ fields: {}, form: ["Wrong email or password."] });
  });

  it("maps register's 422 codes onto their fields", () => {
    for (const [code, field] of [
      ["password_too_short", "password"],
      ["password_matches_email", "password"],
      ["email_taken", "email"],
    ] as const) {
      const err = toApiError({ error: { code, message: code, field_errors: [{ field, code, message: `msg ${code}` }] } }, 422);
      expect(formErrors(err, ["display_name", "email", "password"] as const).fields).toEqual({ [field]: `msg ${code}` });
    }
  });

  it("reads Retry-After and says how long to wait on 429", () => {
    const err = toApiError({ error: { code: "rate_limited", message: "Slow down." } }, 429, new Headers({ "retry-after": "30" }));
    expect(err.retryAfterSeconds).toBe(30);
    expect(displayMessage(err)).toBe("Too many attempts, try again in 30 seconds.");
    expect(formErrors(err, ["email"] as const).form).toEqual(["Too many attempts, try again in 30 seconds."]);
    expect(displayMessage({ ...err, retryAfterSeconds: 1 })).toBe("Too many attempts, try again in 1 second.");
    expect(displayMessage({ ...err, retryAfterSeconds: undefined })).toBe("Too many attempts, try again in a moment.");
    expect(parseRetryAfter("Wed, 21 Oct 2015 07:28:10 GMT", Date.parse("Wed, 21 Oct 2015 07:28:00 GMT"))).toBe(10);
    expect(parseRetryAfter("soon")).toBeUndefined();
  });
});

describe("workspace helpers", () => {
  it("derives slugs the API accepts", () => {
    expect(slugify("Acme Social, Inc.")).toBe("acme-social-inc");
    expect(slugify("Café Münch")).toBe("cafe-munch");
    expect(slugify("  --  ")).toBe("");
    const long = slugify("a".repeat(47) + " b");
    expect(long.length).toBeLessThanOrEqual(48);
    expect(SLUG_PATTERN.test(long)).toBe(true);
  });

  it("makes two-letter marks", () => {
    expect(initials("Acme")).toBe("AC");
    expect(initials("Personal Brand")).toBe("PB");
    expect(initials("")).toBe("?");
  });

  it("derives API key state", () => {
    const now = Date.parse("2026-11-08T00:00:00Z");
    expect(apiKeyState({ revoked_at: "2026-01-01T00:00:00Z", expires_at: null }, now)).toBe("revoked");
    expect(apiKeyState({ revoked_at: null, expires_at: "2026-11-01T00:00:00Z" }, now)).toBe("expired");
    expect(apiKeyState({ revoked_at: null, expires_at: null }, now)).toBe("active");
  });
});

describe("OAuth with PKCE", () => {
  const ORIGIN = "http://localhost:3000";

  it("computes the RFC 7636 S256 challenge: unpadded base64url of SHA-256", async () => {
    const verifier = "dBjftJeZ4CVP-mJ92IZBnmuB5x5uY4NvgK7Gna3IpRw";
    const expected = createHash("sha256").update(verifier).digest("base64url");
    expect(await pkceChallenge(verifier)).toBe(expected);
    expect(expected).not.toMatch(/[+/=]/);
  });

  it("start stores the verifier in a Lax httpOnly cookie and redirects to the API's authorize endpoint", async () => {
    vi.stubEnv("MERA_API_URL", "http://api.test");
    try {
      const res = await handleOAuthStart(new NextRequest(`${ORIGIN}/api/session/oauth/google/start?next=/w/acme/team`), "google");
      const location = new URL(res.headers.get("location")!);
      expect(`${location.origin}${location.pathname}`).toBe("http://api.test/api/v1/auth/oauth/google/authorize");
      expect(location.searchParams.get("redirect_uri")).toBe(`${ORIGIN}/api/session/oauth/callback`);
      expect(location.searchParams.get("code_challenge_method")).toBe("S256");
      const cookie = res.headers.getSetCookie().find((c) => c.startsWith("mera_oauth="))!;
      expect(cookie).toMatch(/HttpOnly/i);
      expect(cookie).toMatch(/SameSite=lax/i);
      const stored = JSON.parse(decodeURIComponent(cookie.split(";")[0]!.slice("mera_oauth=".length))) as { v: string; next: string };
      expect(stored.next).toBe("/w/acme/team");
      expect(await pkceChallenge(stored.v)).toBe(location.searchParams.get("code_challenge"));
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it("start refuses unknown providers", async () => {
    const res = await handleOAuthStart(new NextRequest(`${ORIGIN}/api/session/oauth/evil/start`), "evil");
    expect(new URL(res.headers.get("location")!).pathname).toBe("/login");
  });

  function callback(query: string, verifierCookie?: string) {
    const headers: Record<string, string> = verifierCookie ? { cookie: `mera_oauth=${encodeURIComponent(verifierCookie)}` } : {};
    return new NextRequest(`${ORIGIN}/api/session/oauth/callback${query}`, { headers });
  }
  const verifier = JSON.stringify({ v: "v".repeat(43), next: "/w/acme/team" });

  it("callback exchanges the code with the stored verifier and sets the session cookies", async () => {
    const upstream = vi.fn(async (req: Request) => {
      expect(new URL(req.url).pathname).toBe("/api/v1/auth/oauth/exchange");
      expect(await req.json()).toEqual({ code: "code-1234567890", code_verifier: "v".repeat(43) });
      return Response.json({ access_token: "at", refresh_token: "rt", expires_in: 900 });
    });
    const res = await handleOAuthCallback(callback("?code=code-1234567890", verifier), {
      apiBaseUrl: "http://api.test",
      fetch: upstream as unknown as typeof fetch,
    });
    expect(res.status).toBe(200);
    expect(await res.text()).toContain('url=/w/acme/team"');
    const cookies = res.headers.getSetCookie();
    expect(cookies.some((c) => c.startsWith("mera_at=at;"))).toBe(true);
    expect(cookies.some((c) => c.startsWith("mera_rt=rt;"))).toBe(true);
    expect(cookies.find((c) => c.startsWith("mera_oauth="))).toMatch(/Max-Age=0/i);
  });

  it("callback sends provider errors back to /login with a message code", async () => {
    const res = await handleOAuthCallback(callback("?error=account_exists", verifier));
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("oauth_error")).toBe("account_exists");
    expect(location.searchParams.get("next")).toBe("/w/acme/team");
  });

  it("callback refuses a code without the verifier cookie (injected from another browser)", async () => {
    const upstream = vi.fn();
    const res = await handleOAuthCallback(callback("?code=code-1234567890"), { fetch: upstream as unknown as typeof fetch });
    expect(new URL(res.headers.get("location")!).searchParams.get("oauth_error")).toBe("oauth_failed");
    expect(upstream).not.toHaveBeenCalled();
  });
});
