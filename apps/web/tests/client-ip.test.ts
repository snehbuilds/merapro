import { afterEach, describe, expect, it, vi } from "vitest";
import { clientIp, trustedHeaders } from "@/lib/session/client-ip";
import { handleLogin, handleRegister } from "@/lib/session/session-routes";

describe("clientIp", () => {
  it("takes the right-most X-Forwarded-For entry", () => {
    expect(clientIp(new Headers({ "x-forwarded-for": "198.51.100.1, 203.0.113.9" }))).toBe("203.0.113.9");
    expect(clientIp(new Headers({ "x-forwarded-for": "::ffff:203.0.113.9" }))).toBe("::ffff:203.0.113.9");
    expect(clientIp(new Headers({ "x-forwarded-for": "2001:db8::1 , " }))).toBe("2001:db8::1");
  });

  it("returns null for nothing or junk", () => {
    expect(clientIp(new Headers())).toBeNull();
    expect(clientIp(new Headers({ "x-forwarded-for": " , " }))).toBeNull();
    expect(clientIp(new Headers({ "x-forwarded-for": "evil.example" }))).toBeNull();
    expect(clientIp(new Headers({ "x-forwarded-for": "1".repeat(60) }))).toBeNull();
  });

  it("sends nothing without a secret", () => {
    expect(trustedHeaders(new Headers({ "x-forwarded-for": "203.0.113.9" }), undefined)).toEqual({});
    expect(trustedHeaders(new Headers(), "s")).toEqual({ "x-mera-proxy-secret": "s" });
  });
});

describe("login and register through the BFF", () => {
  afterEach(() => vi.restoreAllMocks());

  function upstream() {
    const seen: Request[] = [];
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const req = input instanceof Request ? input : new Request(input);
      seen.push(req);
      if (req.url.endsWith("/auth/register")) {
        return new Response(JSON.stringify({ id: "u1" }), { status: 201, headers: { "content-type": "application/json" } });
      }
      return new Response(JSON.stringify({ access_token: "a", refresh_token: "r", expires_in: 900, token_type: "bearer" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    return { seen, fetch: fetch as unknown as typeof globalThis.fetch };
  }

  function browser(path: string, body: unknown) {
    return new Request(`http://web.test${path}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-mera-client": "web",
        origin: "http://web.test",
        "x-forwarded-for": "203.0.113.9",
      },
      body: JSON.stringify(body),
    });
  }

  it("passes the browser's address on login and on register's sign-in", async () => {
    const up = upstream();
    const deps = { apiBaseUrl: "http://api.test", fetch: up.fetch, proxySecret: "s3cret" };
    const res = await handleLogin(browser("/api/session/login", { email: "a@b.c", password: "pw" }), deps);
    expect(res.status).toBe(204);
    const reg = await handleRegister(
      browser("/api/session/register", { email: "a@b.c", password: "pw", display_name: "A" }),
      deps,
    );
    expect(reg.status).toBe(201);
    expect(up.seen.map((r) => [new URL(r.url).pathname, r.headers.get("x-mera-client-ip"), r.headers.get("x-mera-proxy-secret")])).toEqual([
      ["/api/v1/auth/login", "203.0.113.9", "s3cret"],
      ["/api/v1/auth/register", "203.0.113.9", "s3cret"],
      ["/api/v1/auth/login", "203.0.113.9", "s3cret"],
    ]);
  });
});
