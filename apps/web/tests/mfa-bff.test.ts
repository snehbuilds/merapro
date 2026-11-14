import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";
import { handleMfa, mfaCookieName } from "@/lib/session/mfa";
import { handleLogin } from "@/lib/session/session-routes";

function req(path: string, body: unknown, cookie?: string) {
  return new NextRequest(`http://web.test${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-mera-client": "web",
      origin: "http://web.test",
      ...(cookie ? { cookie } : {}),
    },
    body: JSON.stringify(body),
  });
}

function api(handler: (url: string, body: unknown) => Response) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const r = input instanceof Request ? input : new Request(input);
    return handler(r.url, r.method === "POST" ? await r.json().catch(() => null) : null);
  }) as unknown as typeof globalThis.fetch;
}

describe("two-step sign-in through the BFF (P2-M2.3)", () => {
  it("keeps the challenge in an httpOnly cookie and never shows it to the page", async () => {
    const fetch = api(() =>
      Response.json({ error: { code: "mfa_required", message: "Enter the code", detail: "mera_mfa_secret_token" } }, { status: 401 }),
    );
    const res = await handleLogin(req("/api/session/login", { email: "a@b.c", password: "pw" }), { apiBaseUrl: "http://api.test", fetch });
    expect(res.status).toBe(401);
    const body = await res.text();
    expect(body).toContain("mfa_required");
    expect(body).not.toContain("mera_mfa_secret_token");
    const cookie = res.headers.get("set-cookie") ?? "";
    expect(cookie).toContain(`${mfaCookieName(false)}=mera_mfa_secret_token`);
    expect(cookie.toLowerCase()).toContain("httponly");
    expect(cookie.toLowerCase()).toContain("samesite=strict");
  });

  it("redeems the cookie with the code and sets the session", async () => {
    let sent: unknown = null;
    const fetch = api((url, body) => {
      sent = body;
      expect(url).toBe("http://api.test/api/v1/auth/mfa/verify");
      return Response.json({ access_token: "a", refresh_token: "r", expires_in: 900, token_type: "bearer" });
    });
    const res = await handleMfa(req("/api/session/mfa", { code: "123456" }, `${mfaCookieName(false)}=mera_mfa_tok`), {
      apiBaseUrl: "http://api.test",
      fetch,
    });
    expect(res.status).toBe(204);
    expect(sent).toEqual({ mfa_token: "mera_mfa_tok", code: "123456" });
    const cookies = res.headers.get("set-cookie") ?? "";
    expect(cookies).toContain("mera_at=");
    expect(cookies).toContain(`${mfaCookieName(false)}=;`);
  });

  it("refuses without a challenge, and clears a burned one", async () => {
    const none = await handleMfa(req("/api/session/mfa", { code: "1" }), { apiBaseUrl: "http://api.test", fetch: api(() => new Response()) });
    expect(none.status).toBe(401);
    const fetch = api(() => Response.json({ error: { code: "invalid_mfa_challenge", message: "expired" } }, { status: 401 }));
    const burned = await handleMfa(req("/api/session/mfa", { code: "1" }, `${mfaCookieName(false)}=x`), { apiBaseUrl: "http://api.test", fetch });
    expect(burned.status).toBe(401);
    expect(burned.headers.get("set-cookie") ?? "").toContain(`${mfaCookieName(false)}=;`);
  });
});
