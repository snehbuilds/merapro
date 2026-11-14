import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";
import { POST as proxyPost, GET as proxyGet } from "@/app/api/mera/[...path]/route";
import { POST as loginPost } from "@/app/api/session/login/route";
import { POST as logoutPost } from "@/app/api/session/logout/route";
import { POST as registerPost } from "@/app/api/session/register/route";
import { config as proxyConfig } from "@/proxy";
import { displayMessage, toApiError } from "@/lib/api/errors";
import { sessionCookieWrites } from "@/lib/session/cookies";
import { csrfRejection } from "@/lib/session/csrf";
import { gateRequest } from "@/lib/session/gate";
import { handleLogin, handleRegister } from "@/lib/session/session-routes";

const API = "http://api.test";
const ORIGIN = "http://localhost:3000";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function post(path: string, body: unknown, headers: Record<string, string> = { "x-mera-client": "web" }) {
  return new NextRequest(`${ORIGIN}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "content-type": "application/json", ...headers },
  });
}

describe("CSRF header", () => {
  it("lets safe methods through and requires X-Mera-Client: web for the rest", () => {
    const h = (v?: string) => new Headers(v ? { "x-mera-client": v } : {});
    expect(csrfRejection({ method: "GET", headers: h() })).toBeNull();
    expect(csrfRejection({ method: "HEAD", headers: h() })).toBeNull();
    expect(csrfRejection({ method: "POST", headers: h("web") })).toBeNull();
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      expect(csrfRejection({ method, headers: h() })?.status).toBe(403);
      expect(csrfRejection({ method, headers: h("mobile") })?.status).toBe(403);
    }
  });

  it("rejects non-GET /api/mera and /api/session requests without the header, before anything else", async () => {
    const noHeader = {};
    const ctx = { params: Promise.resolve({ path: ["workspaces"] }) };
    for (const res of [
      await proxyPost(post("/api/mera/workspaces", {}, noHeader), ctx),
      await loginPost(post("/api/session/login", { email: "a@b.c", password: "x" }, noHeader)),
      await registerPost(post("/api/session/register", {}, noHeader)),
      await logoutPost(post("/api/session/logout", {}, noHeader)),
    ]) {
      expect(res.status).toBe(403);
      expect(((await res.json()) as { error: { code: string } }).error.code).toBe("csrf_rejected");
    }
  });

  it("does not require the header for GET through the proxy", async () => {
    // No cookies, nothing listening on the default API URL in tests: anything
    // but 403 proves the CSRF gate let it through.
    vi.stubEnv("MERA_API_URL", "http://127.0.0.1:9");
    try {
      const res = await proxyGet(new NextRequest(`${ORIGIN}/api/mera/workspaces`), {
        params: Promise.resolve({ path: ["workspaces"] }),
      });
      expect(res.status).not.toBe(403);
    } finally {
      vi.unstubAllEnvs();
    }
  });
});

describe("session routes", () => {
  it("login stores the tokens in httpOnly SameSite=Strict cookies and returns no tokens", async () => {
    const upstream = vi.fn(async () => json(200, { access_token: "at", refresh_token: "rt", expires_in: 900, token_type: "bearer" }));
    const res = await handleLogin(post("/api/session/login", { email: "a@b.c", password: "pw" }), {
      apiBaseUrl: API,
      fetch: upstream as unknown as typeof fetch,
    });
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
    const cookies = res.headers.getSetCookie();
    expect(cookies).toHaveLength(2);
    for (const c of cookies) {
      expect(c).toMatch(/HttpOnly/i);
      expect(c).toMatch(/SameSite=strict/i);
      expect(c).toMatch(/Path=\//);
    }
    expect(cookies.find((c) => c.startsWith("mera_at="))).toMatch(/^mera_at=at;/);
    expect(cookies.find((c) => c.startsWith("mera_rt="))).toMatch(/^mera_rt=rt;/);
  });

  it("login passes the API's 401 invalid_credentials envelope through and sets no cookies", async () => {
    const body = { error: { code: "invalid_credentials", message: "Email or password is wrong.", field_errors: [] } };
    const upstream = vi.fn(async () => json(401, body));
    const res = await handleLogin(post("/api/session/login", { email: "a@b.c", password: "pw" }), {
      apiBaseUrl: API,
      fetch: upstream as unknown as typeof fetch,
    });
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual(body);
    expect(res.headers.getSetCookie()).toHaveLength(0);
  });

  it("login passes the per-account 429 throttle through with Retry-After, so the form can say how long to wait", async () => {
    const body = { error: { code: "rate_limited", message: "Too many sign-in attempts.", field_errors: [] } };
    const upstream = vi.fn(
      async () => new Response(JSON.stringify(body), { status: 429, headers: { "content-type": "application/json", "retry-after": "17" } }),
    );
    const res = await handleLogin(post("/api/session/login", { email: "a@b.c", password: "pw" }), {
      apiBaseUrl: API,
      fetch: upstream as unknown as typeof fetch,
    });
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("17");
    expect(res.headers.getSetCookie()).toHaveLength(0);
    const err = toApiError(await res.json(), res.status, res.headers);
    expect(displayMessage(err)).toBe("Too many attempts, try again in 17 seconds.");
  });

  it("session routes never send Idempotency-Key upstream (the API doesn't cache /auth/*)", async () => {
    const upstream = vi.fn(async (req: Request) => {
      expect(req.headers.get("idempotency-key")).toBeNull();
      return json(200, { access_token: "at", refresh_token: "rt", expires_in: 900 });
    });
    await handleLogin(post("/api/session/login", { email: "a@b.c", password: "pw" }, { "x-mera-client": "web", "idempotency-key": "k1" }), {
      apiBaseUrl: API,
      fetch: upstream as unknown as typeof fetch,
    });
    expect(upstream).toHaveBeenCalledTimes(1);
  });

  it("register passes 422 field errors through, and signs in on success", async () => {
    const weak = {
      error: {
        code: "password_too_short",
        message: "Use at least 12 characters.",
        field_errors: [{ field: "password", code: "password_too_short", message: "Use at least 12 characters." }],
      },
    };
    const failing = vi.fn(async () => json(422, weak));
    const bad = await handleRegister(post("/api/session/register", { email: "a@b.c", password: "short", display_name: "A" }), {
      apiBaseUrl: API,
      fetch: failing as unknown as typeof fetch,
    });
    expect(bad.status).toBe(422);
    expect(await bad.json()).toEqual(weak);

    const ok = vi.fn(async (req: Request) =>
      new URL(req.url).pathname.endsWith("/register")
        ? json(201, { id: "u1", email: "a@b.c", display_name: "A" })
        : json(200, { access_token: "at", refresh_token: "rt", expires_in: 900 }),
    );
    const good = await handleRegister(post("/api/session/register", { email: "a@b.c", password: "long enough pw", display_name: "A" }), {
      apiBaseUrl: API,
      fetch: ok as unknown as typeof fetch,
    });
    expect(good.status).toBe(201);
    expect(await good.json()).toEqual({ id: "u1", email: "a@b.c", display_name: "A" });
    expect(good.headers.getSetCookie()).toHaveLength(2);
  });

  it("marks cookies Secure with the __Host- prefix in production", () => {
    const writes = sessionCookieWrites({ access_token: "a", refresh_token: "r", expires_in: 900 }, true);
    expect(writes.map((w) => w.name)).toEqual(["__Host-mera_at", "__Host-mera_rt"]);
    for (const w of writes) expect(w.options).toMatchObject({ httpOnly: true, secure: true, sameSite: "strict", path: "/" });
    expect(sessionCookieWrites({ access_token: "a", refresh_token: "r", expires_in: 900 }, false)[0]!.options.secure).toBe(false);
  });
});

describe("page gate (proxy.ts)", () => {
  it("gates the app but not the auth pages or the email verification link", () => {
    expect(proxyConfig.matcher).toEqual(expect.arrayContaining(["/", "/w/:path*", "/welcome", "/invite/:path*"]));
    for (const open of ["/login", "/register", "/verify-email"]) {
      expect(proxyConfig.matcher.some((m) => m === open || m.startsWith(`${open}/`))).toBe(false);
    }
  });

  it("redirects an unauthenticated workspace request to /login with a safe next", async () => {
    const res = await gateRequest(new NextRequest(`${ORIGIN}/w/acme/team?tab=roles`));
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/w/acme/team?tab=roles");
  });

  it("lets a request with an access cookie through", async () => {
    const res = await gateRequest(new NextRequest(`${ORIGIN}/w/acme/team`, { headers: { cookie: "mera_at=at" } }));
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("x-middleware-next")).toBe("1");
  });

  it("refreshes an expired access cookie and forwards the new one to the page", async () => {
    const upstream = vi.fn(async () => json(200, { access_token: "at-new", refresh_token: "rt-new", expires_in: 900 }));
    const res = await gateRequest(new NextRequest(`${ORIGIN}/w/acme/team`, { headers: { cookie: "mera_rt=rt-gate" } }), {
      apiBaseUrl: API,
      fetch: upstream as unknown as typeof fetch,
    });
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.getSetCookie().some((c) => c.startsWith("mera_at=at-new"))).toBe(true);
    // The rewritten request cookie header is what server components will read.
    expect(res.headers.get("x-middleware-request-cookie")).toContain("mera_at=at-new");
  });

  it("clears cookies and redirects to login when the refresh token is dead", async () => {
    const upstream = vi.fn(async () => json(401, { error: { code: "invalid_refresh_token", message: "x" } }));
    const res = await gateRequest(new NextRequest(`${ORIGIN}/w/acme/dashboard`, { headers: { cookie: "mera_rt=rt-dead" } }), {
      apiBaseUrl: API,
      fetch: upstream as unknown as typeof fetch,
    });
    expect(new URL(res.headers.get("location")!).searchParams.get("next")).toBe("/w/acme/dashboard");
    expect(res.headers.getSetCookie().every((c) => /Max-Age=0/i.test(c))).toBe(true);
  });
});
