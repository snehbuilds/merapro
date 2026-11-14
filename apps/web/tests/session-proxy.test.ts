import { describe, expect, it, vi } from "vitest";
import { proxyToApi, upstreamPath, type ProxyRequest } from "@/lib/session/proxy-core";
import { createRotationStore, refreshSession } from "@/lib/session/refresh";

/*
 * The BFF proxy's refresh-and-retry logic against a mocked upstream. The mock
 * is the `fetch` handed to the SDK client, so these run through the real
 * generated transport.
 */

const API = "http://api.test";

interface Seen {
  method: string;
  path: string;
  search: string;
  auth: string | null;
  headers: Headers;
  body: string;
}

function envelope(code: string, message = code) {
  return { error: { code, message, field_errors: [], request_id: "req-1" } };
}

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

/** A scripted upstream: each handler sees the request and returns a Response. */
function upstream(handler: (seen: Seen) => Response | Promise<Response>) {
  const calls: Seen[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const req = input instanceof Request ? input : new Request(input);
    const url = new URL(req.url);
    const seen: Seen = {
      method: req.method,
      path: url.pathname,
      search: url.search,
      auth: req.headers.get("authorization"),
      headers: req.headers,
      body: req.method === "GET" ? "" : await req.text(),
    };
    calls.push(seen);
    return handler(seen);
  });
  return { fetch: fetchMock as unknown as typeof globalThis.fetch, calls };
}

function request(overrides: Partial<ProxyRequest> = {}): ProxyRequest {
  return {
    method: "GET",
    path: ["workspaces"],
    search: "",
    headers: new Headers(),
    accessToken: "at-old",
    refreshToken: "rt-old",
    ...overrides,
  };
}

const NEW_PAIR = { access_token: "at-new", refresh_token: "rt-new", expires_in: 900, token_type: "bearer" };

describe("upstreamPath", () => {
  it("maps the catch-all to /api/v1 and accepts the SDK's own /api/v1 prefix", () => {
    expect(upstreamPath(["workspaces"])).toBe("/api/v1/workspaces");
    expect(upstreamPath(["api", "v1", "workspaces", "abc", "members"])).toBe("/api/v1/workspaces/abc/members");
  });

  it("refuses paths that could leave /api/v1", () => {
    expect(upstreamPath([])).toBeNull();
    expect(upstreamPath(["api", "v1"])).toBeNull();
    expect(upstreamPath([".."])).toBeNull();
    expect(upstreamPath(["workspaces", "..", "..", "healthz"])).toBeNull();
    expect(upstreamPath(["a/b"])).toBeNull();
    expect(upstreamPath(["a\\b"])).toBeNull();
  });
});

describe("proxyToApi", () => {
  it("forwards with the access cookie as bearer and passes allowlisted headers only", async () => {
    const up = upstream(() => json(200, { data: [] }, { etag: '"v1"', "set-cookie": "evil=1" }));
    const headers = new Headers({
      "content-type": "application/json",
      "idempotency-key": "idem-1",
      "if-match": '"v0"',
      "x-request-id": "rid-1",
      cookie: "mera_at=at-old; mera_rt=rt-old",
      "x-mera-client": "web",
      authorization: "Bearer smuggled",
    });
    const result = await proxyToApi(
      request({ method: "POST", path: ["api", "v1", "workspaces"], search: "?x=1", headers, body: new TextEncoder().encode('{"a":1}').buffer as ArrayBuffer }),
      { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() },
    );

    expect(result.session).toEqual({ kind: "keep" });
    expect(result.response.status).toBe(200);
    expect(await result.response.json()).toEqual({ data: [] });
    expect(result.response.headers.get("etag")).toBe('"v1"');
    expect(result.response.headers.get("set-cookie")).toBeNull();

    const [call] = up.calls;
    expect(call).toMatchObject({ method: "POST", path: "/api/v1/workspaces", search: "?x=1", auth: "Bearer at-old", body: '{"a":1}' });
    expect(call!.headers.get("idempotency-key")).toBe("idem-1");
    expect(call!.headers.get("if-match")).toBe('"v0"');
    expect(call!.headers.get("x-request-id")).toBe("rid-1");
    expect(call!.headers.get("cookie")).toBeNull();
    expect(call!.headers.get("x-mera-client")).toBeNull();
  });

  it("sends the proxy secret upstream when configured, on the first try and on the retry", async () => {
    let n = 0;
    const up = upstream((seen) => {
      if (seen.path === "/api/v1/auth/refresh") return json(200, NEW_PAIR);
      n += 1;
      return n === 1 ? json(401, envelope("invalid_token")) : json(200, {});
    });
    await proxyToApi(
      request({
        method: "POST",
        path: ["workspaces", "ws-1", "social-accounts", "acc-1", "health", "check"],
        headers: new Headers({ "x-mera-client": "web" }),
      }),
      { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore(), proxySecret: "s3cret" },
    );
    const apiCalls = up.calls.filter((c) => c.path !== "/api/v1/auth/refresh");
    expect(apiCalls).toHaveLength(2);
    expect(apiCalls.map((c) => c.headers.get("x-mera-proxy-secret"))).toEqual(["s3cret", "s3cret"]);
  });

  it("never forwards a browser-supplied proxy secret", async () => {
    const forged = new Headers({ "x-mera-proxy-secret": "forged", "x-mera-client": "web" });
    const unset = upstream(() => json(200, {}));
    await proxyToApi(request({ headers: forged }), { apiBaseUrl: API, fetch: unset.fetch, store: createRotationStore() });
    expect(unset.calls[0]!.headers.get("x-mera-proxy-secret")).toBeNull();

    const set = upstream(() => json(200, {}));
    await proxyToApi(request({ headers: forged }), {
      apiBaseUrl: API,
      fetch: set.fetch,
      store: createRotationStore(),
      proxySecret: "s3cret",
    });
    expect(set.calls[0]!.headers.get("x-mera-proxy-secret")).toBe("s3cret");
  });

  it("sends the browser's address with the secret, on the call and on the refresh", async () => {
    let n = 0;
    const up = upstream((seen) => {
      if (seen.path === "/api/v1/auth/refresh") return json(200, NEW_PAIR);
      n += 1;
      return n === 1 ? json(401, envelope("invalid_token")) : json(200, {});
    });
    const headers = new Headers({
      // A client-claimed entry on the left, the reverse proxy's on the right.
      "x-forwarded-for": "198.51.100.1, 203.0.113.9",
      "x-mera-client-ip": "198.51.100.77",
    });
    await proxyToApi(request({ headers }), {
      apiBaseUrl: API,
      fetch: up.fetch,
      store: createRotationStore(),
      proxySecret: "s3cret",
    });
    expect(up.calls.map((c) => [c.path, c.headers.get("x-mera-client-ip")])).toEqual([
      ["/api/v1/workspaces", "203.0.113.9"],
      ["/api/v1/auth/refresh", "203.0.113.9"],
      ["/api/v1/workspaces", "203.0.113.9"],
    ]);
    expect(up.calls[1]!.headers.get("x-mera-proxy-secret")).toBe("s3cret");
    expect(up.calls.every((c) => c.headers.get("x-forwarded-for") === null)).toBe(true);

    // Without a secret the API would not believe it, so it is not sent at all.
    const plain = upstream(() => json(200, {}));
    await proxyToApi(request({ headers }), { apiBaseUrl: API, fetch: plain.fetch, store: createRotationStore() });
    expect(plain.calls[0]!.headers.get("x-mera-client-ip")).toBeNull();
  });

  it("passes non-401 errors through untouched, without refreshing", async () => {
    const up = upstream(() => json(403, envelope("forbidden")));
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });
    expect(result.response.status).toBe(403);
    expect(await result.response.json()).toEqual(envelope("forbidden"));
    expect(result.session).toEqual({ kind: "keep" });
    expect(up.calls).toHaveLength(1);
  });

  it("on 401 invalid_token refreshes once, retries with the new token and writes the new pair", async () => {
    const up = upstream((seen) => {
      if (seen.path === "/api/v1/auth/refresh") return json(200, NEW_PAIR);
      return seen.auth === "Bearer at-new" ? json(200, { data: ["ok"] }) : json(401, envelope("invalid_token"));
    });
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });

    expect(result.response.status).toBe(200);
    expect(await result.response.json()).toEqual({ data: ["ok"] });
    expect(result.session).toEqual({ kind: "set", pair: { access_token: "at-new", refresh_token: "rt-new", expires_in: 900 } });
    expect(up.calls.map((c) => c.path)).toEqual(["/api/v1/workspaces", "/api/v1/auth/refresh", "/api/v1/workspaces"]);
    expect(JSON.parse(up.calls[1]!.body)).toEqual({ refresh_token: "rt-old" });
  });

  it("re-sends the original body on the retry", async () => {
    const up = upstream((seen) => {
      if (seen.path === "/api/v1/auth/refresh") return json(200, NEW_PAIR);
      return seen.auth === "Bearer at-new" ? json(201, { id: "w1" }) : json(401, envelope("invalid_token"));
    });
    const body = new TextEncoder().encode('{"name":"Acme"}').buffer as ArrayBuffer;
    await proxyToApi(request({ method: "POST", body, headers: new Headers({ "content-type": "application/json" }) }), {
      apiBaseUrl: API,
      fetch: up.fetch,
      store: createRotationStore(),
    });
    expect(up.calls.filter((c) => c.path === "/api/v1/workspaces").map((c) => c.body)).toEqual(['{"name":"Acme"}', '{"name":"Acme"}']);
  });

  it("does not refresh for a 401 with another code", async () => {
    const up = upstream(() => json(401, envelope("invalid_credentials")));
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });
    expect(result.response.status).toBe(401);
    expect(up.calls).toHaveLength(1);
    expect(result.session).toEqual({ kind: "keep" });
  });

  it("clears cookies and returns 401 when the refresh fails", async () => {
    const up = upstream((seen) =>
      seen.path === "/api/v1/auth/refresh" ? json(401, envelope("invalid_refresh_token")) : json(401, envelope("invalid_token")),
    );
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });
    expect(result.response.status).toBe(401);
    expect(((await result.response.json()) as { error: { code: string } }).error.code).toBe("invalid_token");
    expect(result.session).toEqual({ kind: "clear" });
    expect(up.calls).toHaveLength(2); // original + refresh, no retry
  });

  it("clears a stale access cookie when there is no refresh cookie", async () => {
    const up = upstream(() => json(401, envelope("invalid_token")));
    const result = await proxyToApi(request({ refreshToken: undefined }), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });
    expect(result.response.status).toBe(401);
    expect(result.session).toEqual({ kind: "clear" });
    expect(up.calls).toHaveLength(1);
  });

  it("on 409 refresh_in_progress waits ~300 ms and retries with the pair another tab just got", async () => {
    const store = createRotationStore();
    const sleep = vi.fn(async () => {
      // While we wait, the other tab's refresh lands in this process.
      store.rotated.set("rt-old", { pair: { access_token: "at-tab2", refresh_token: "rt-tab2", expires_in: 900 }, until: Date.now() + 10_000 });
    });
    const up = upstream((seen) => {
      if (seen.path === "/api/v1/auth/refresh") return json(409, envelope("refresh_in_progress"));
      return seen.auth === "Bearer at-tab2" ? json(200, { data: [] }) : json(401, envelope("invalid_token"));
    });
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: up.fetch, store, sleep });

    expect(sleep).toHaveBeenCalledWith(300);
    expect(result.response.status).toBe(200);
    expect(result.session).toEqual({ kind: "set", pair: { access_token: "at-tab2", refresh_token: "rt-tab2", expires_in: 900 } });
    expect(up.calls.map((c) => c.auth)).toEqual(["Bearer at-old", null, "Bearer at-tab2"]);
  });

  it("on 409 with no newer pair known, retries once with the current cookies and never clears the session", async () => {
    const sleep = vi.fn(async () => {});
    const up = upstream((seen) =>
      seen.path === "/api/v1/auth/refresh" ? json(409, envelope("refresh_in_progress")) : json(401, envelope("invalid_token")),
    );
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore(), sleep });
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(up.calls).toHaveLength(3);
    expect(result.response.status).toBe(401);
    expect(result.session).toEqual({ kind: "keep" });
  });

  it("uses a pair rotated moments ago instead of presenting the old refresh token again", async () => {
    const store = createRotationStore();
    store.rotated.set("rt-old", { pair: { access_token: "at-new", refresh_token: "rt-new", expires_in: 900 }, until: Date.now() + 10_000 });
    const up = upstream((seen) => (seen.auth === "Bearer at-new" ? json(200, {}) : json(401, envelope("invalid_token"))));
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: up.fetch, store });
    expect(up.calls.map((c) => c.path)).toEqual(["/api/v1/workspaces"]);
    expect(result.session.kind).toBe("set");
  });

  it("returns 502 when the API is unreachable, keeping the session", async () => {
    const failing = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof globalThis.fetch;
    const result = await proxyToApi(request(), { apiBaseUrl: API, fetch: failing, store: createRotationStore() });
    expect(result.response.status).toBe(502);
    expect(((await result.response.json()) as { error: { code: string } }).error.code).toBe("upstream_unavailable");
    expect(result.session).toEqual({ kind: "keep" });
  });

  it("returns 204 bodies as empty", async () => {
    const up = upstream(() => new Response(null, { status: 204 }));
    const result = await proxyToApi(request({ method: "DELETE" }), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });
    expect(result.response.status).toBe(204);
    expect(await result.response.text()).toBe("");
  });

  it("404s an unacceptable path without calling upstream", async () => {
    const up = upstream(() => json(200, {}));
    const result = await proxyToApi(request({ path: [".."] }), { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });
    expect(result.response.status).toBe(404);
    expect(up.calls).toHaveLength(0);
  });
});

describe("refreshSession", () => {
  it("collapses concurrent refreshes of one token into one upstream call", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const up = upstream(async () => {
      await gate;
      return json(200, NEW_PAIR);
    });
    const store = createRotationStore();
    const a = refreshSession("rt-old", { apiBaseUrl: API, fetch: up.fetch, store });
    const b = refreshSession("rt-old", { apiBaseUrl: API, fetch: up.fetch, store });
    release();
    const [ra, rb] = await Promise.all([a, b]);
    expect(up.calls).toHaveLength(1);
    expect(ra).toEqual(rb);
    expect(ra.kind).toBe("ok");
    // Remembered for a later caller still holding the old token.
    const later = await refreshSession("rt-old", { apiBaseUrl: API, fetch: up.fetch, store });
    expect(later).toEqual(ra);
    expect(up.calls).toHaveLength(1);
  });

  it("forgets a rotation after the grace window", async () => {
    let now = 1_000_000;
    const up = upstream(() => json(200, NEW_PAIR));
    const store = createRotationStore();
    await refreshSession("rt-old", { apiBaseUrl: API, fetch: up.fetch, store, now: () => now });
    now += 25_000;
    await refreshSession("rt-old", { apiBaseUrl: API, fetch: up.fetch, store, now: () => now });
    expect(up.calls).toHaveLength(2);
  });

  it("classifies outcomes: 409 in progress, 401 failed, 5xx unavailable", async () => {
    const cases: [Response, string][] = [
      [json(409, envelope("refresh_in_progress")), "in_progress"],
      [json(401, envelope("invalid_refresh_token")), "failed"],
      [json(503, envelope("service_unavailable")), "unavailable"],
    ];
    for (const [response, kind] of cases) {
      const up = upstream(() => response);
      const outcome = await refreshSession("rt", { apiBaseUrl: API, fetch: up.fetch, store: createRotationStore() });
      expect(outcome.kind).toBe(kind);
    }
  });
});
