/* eslint-disable @typescript-eslint/no-explicit-any -- JSON-RPC results and Graph payloads are walked as untyped JSON here */
import { createHmac } from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/*
 * Phase 2 journeys in a real browser (P2-M10.2), against the verify stack:
 *
 * - MCP: a client drafts a post for a group and sends it for approval over Mera's MCP
 *   server; a person approves it in the web queue. MCP itself approves nothing.
 * - Inbox: comments are fetched (sandbox platforms), and the Engagement agent's draft is
 *   approved and sent from the Inbox screen, as the account that was commented on.
 * - WhatsApp: a person pairs WhatsApp from the Channels screen and approves from a card.
 *   Mera talks to a fake Graph API (e2e/support/fake-whatsapp-graph.mjs) and receives
 *   webhooks signed exactly as Meta signs them.
 *
 * Extra settings over approval-loop.spec.ts (defaults match .claude/launch.json):
 *   E2E_MCP_URL, E2E_BOTS_URL, E2E_FAKE_GRAPH_URL, E2E_WA_APP_SECRET
 */

const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const MCP = process.env.E2E_MCP_URL ?? "http://127.0.0.1:8002/mcp";
const BOTS = process.env.E2E_BOTS_URL ?? "http://127.0.0.1:8011";
const GRAPH = process.env.E2E_FAKE_GRAPH_URL ?? "http://127.0.0.1:8795";
const WA_SECRET = process.env.E2E_WA_APP_SECRET ?? "e2e-wa-app-secret-0123456789";
const EMAIL = process.env.E2E_EMAIL ?? "demo@example.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Mera demo password 2026!";
const WORKSPACE = process.env.E2E_WORKSPACE ?? "acme";
const GROUP = process.env.E2E_GROUP ?? "Tech News";
const PIN = "482916";

const WORDS = (
  "harbor lantern orchard granite meadow compass ember quartz willow canyon falcon prism glacier " +
  "saffron tundra beacon cobalt juniper marble nectar orbit pebble quiver ripple summit thistle"
).split(" ");
const fresh = (n: number) =>
  [...new Set(Array.from({ length: n * 3 }, () => WORDS[Math.floor(Math.random() * WORDS.length)]!))].slice(0, n).join(", ");

interface Variant {
  id: string;
  nickname: string;
  state: string;
  enabled: boolean;
  current_version: { id: string } | null;
}
interface PostSet {
  id: string;
  generation_status: string;
  variants: Variant[];
}

async function apiAs(request: APIRequestContext) {
  const login = await request.post(`${API}/api/v1/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
  expect(login.ok(), await login.text()).toBe(true);
  const { access_token } = (await login.json()) as { access_token: string };
  const headers = { authorization: `Bearer ${access_token}` };
  const call = async <T>(method: "GET" | "POST" | "PUT" | "DELETE", path: string, data?: unknown): Promise<T> => {
    const go = () => request.fetch(`${API}/api/v1${path}`, { method, headers, data });
    const r = await go().catch(go); // uvicorn drops idle keep-alive sockets after 5 s
    expect(r.ok(), `${method} ${path}: ${await r.text()}`).toBe(true);
    return (r.status() === 204 ? null : await r.json()) as T;
  };
  return {
    get: <T>(p: string) => call<T>("GET", p),
    post: <T>(p: string, d: unknown = {}) => call<T>("POST", p, d),
    put: <T>(p: string, d: unknown) => call<T>("PUT", p, d),
    delete: (p: string) => call<null>("DELETE", p),
  };
}
type Api = Awaited<ReturnType<typeof apiAs>>;

async function workspaceId(api: Api): Promise<string> {
  const { data } = await api.get<{ data: { id: string; slug: string }[] }>("/workspaces");
  const ws = data.find((w) => w.slug === WORKSPACE);
  expect(ws, `workspace ${WORKSPACE} (run the seed)`).toBeTruthy();
  return ws!.id;
}

async function waitForDrafts(api: Api, ws: string, setId: string): Promise<Variant[]> {
  let set: PostSet | null = null;
  await expect
    .poll(
      async () => {
        set = await api.get<PostSet>(`/workspaces/${ws}/post-sets/${setId}`);
        if (set.generation_status === "failed") throw new Error("drafting failed");
        return set.generation_status === "idle" && set.variants.filter((v) => v.enabled).every((v) => v.current_version);
      },
      { timeout: 120_000, intervals: [1_000, 2_000, 3_000] },
    )
    .toBe(true);
  return (set as PostSet | null)!.variants.filter((v) => v.enabled);
}

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/w\//);
}

async function cancelQueued(api: Api, ws: string, setId: string) {
  const { data } = await api.get<{ data: { id: string; post_set_id: string }[] }>(`/workspaces/${ws}/scheduled-posts?state=queued`);
  for (const s of data.filter((x) => x.post_set_id === setId)) await api.delete(`/workspaces/${ws}/scheduled-posts/${s.id}`);
}

let cleanup: Array<() => Promise<void>> = [];
test.afterEach(async () => {
  for (const c of cleanup.reverse()) await c().catch(() => undefined);
  cleanup = [];
});

// --------------------------------------------------------------------------- MCP

test("MCP: a client drafts and sends for approval; a person approves in the web", async ({ page, request }) => {
  const api = await apiAs(request);
  const ws = await workspaceId(api);
  await api.put("/auth/pin", { pin: PIN, proof: PASSWORD });
  const key = await api.post<{ secret: string; key: { id: string } }>(`/workspaces/${ws}/api-keys`, {
    name: `e2e-mcp-${Date.now()}`,
    scopes: ["drafts:read", "drafts:write", "groups:read", "approvals:read"],
  });
  cleanup.push(async () => {
    await api.delete(`/workspaces/${ws}/api-keys/${key.key.id}`);
  });

  let id = 0;
  const rpc = async (method: string, params: Record<string, unknown> = {}) => {
    const r = await request.post(MCP, {
      headers: { authorization: `Bearer ${key.secret}` },
      data: { jsonrpc: "2.0", id: ++id, method, params },
    });
    expect(r.ok(), `${method}: ${r.status()} ${await r.text()}`).toBe(true);
    const body = (await r.json()) as { result?: Record<string, unknown>; error?: unknown };
    expect(body.error, JSON.stringify(body.error)).toBeUndefined();
    return body.result as Record<string, any>;
  };
  const call = async (name: string, args: Record<string, unknown>) => {
    const out = await rpc("tools/call", { name, arguments: args });
    expect(out.isError, JSON.stringify(out.content)).toBe(false);
    return out.structuredContent as Record<string, any>;
  };

  const init = await rpc("initialize", { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "e2e", version: "1" } });
  expect(init.serverInfo.name).toBe("mera");
  const tools = ((await rpc("tools/list")).tools as { name: string }[]).map((t) => t.name);
  expect(tools).toContain("create_post");
  expect(tools.filter((n) => /approve|publish|resume|credential/.test(n)), "no tool approves or publishes").toEqual([]);

  const groups = (await call("list_groups", {})).data as { id: string; name: string }[];
  const group = groups.find((g) => g.name === GROUP);
  expect(group, `group ${GROUP}`).toBeTruthy();
  const title = `E2E MCP ${new Date().toISOString()}`;
  const created = await call("create_post", {
    title,
    topic: `Notes from a client session: ${fresh(4)}.`,
    link: "https://example.com/e2e/mcp",
    account_group_ids: [group!.id],
  });
  const setId = created.post_set_id as string;
  cleanup.push(() => cancelQueued(api, ws, setId));
  const accounts = await waitForDrafts(api, ws, setId);
  const asked = await call("request_approval", { post_set_id: setId });

  // Pending until a person decides: MCP approved nothing.
  const reqPath = `/workspaces/${ws}/approval-requests/${asked.approval_request_id}`;
  expect((await api.get<{ state: string }>(reqPath)).state).toBe("pending");

  await signIn(page);
  await page.goto(`/w/${WORKSPACE}/approvals`);
  const card = page.getByRole("region", { name: title });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: new RegExp(`^Approve all ${accounts.length}$`) }).click();
  const dialog = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("radio", { name: "PIN" }).click();
  await dialog.getByLabel("Approval PIN").fill(PIN);
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(card).toBeHidden({ timeout: 30_000 });
  expect((await api.get<{ state: string }>(reqPath)).state).toBe("approved");

  // The MCP calls are audited as such (the mcp service proves itself to the API).
  const audit = await api.get<{ data: { action: string; surface: string; subject_id: string | null }[] }>(
    `/workspaces/${ws}/audit-log?surface=mcp&limit=100`,
  );
  expect(audit.data.some((e) => e.action === "post_set.created" && e.subject_id === setId)).toBe(true);
  await page.screenshot({ path: "test-results/phase2-mcp-approved.png", fullPage: true });
});

// ------------------------------------------------------------------------- Inbox

test("Inbox: the agent's draft is approved and sent as the commented account", async ({ page, request }) => {
  const api = await apiAs(request);
  const ws = await workspaceId(api);
  // Sandbox platforms: fetch comments on the recent posts now instead of waiting for the poll.
  await api.post(`/workspaces/${ws}/sandbox/poll-comments`);
  interface Summary {
    states: Record<string, number>;
  }
  const before = (await api.get<Summary>(`/workspaces/${ws}/inbox/summary`)).states.replied ?? 0;

  await signIn(page);
  await page.goto(`/w/${WORKSPACE}/inbox?state=triaged`);
  const list = page.getByRole("list", { name: "Comments" });
  await expect(list).toBeVisible();
  await list.getByRole("button", { name: /How long did the migration take/ }).first().click();
  await expect(page.getByRole("heading", { name: /Replies as / })).toBeVisible();
  await page.getByRole("button", { name: "Approve and send" }).first().click();
  await expect(page.getByText(/^Approved\. Sending as /)).toBeVisible();

  // The worker sends it and the comment becomes "replied".
  await expect
    .poll(async () => (await api.get<Summary>(`/workspaces/${ws}/inbox/summary`)).states.replied ?? 0, {
      timeout: 90_000,
      intervals: [2_000],
    })
    .toBeGreaterThan(before);
  interface Comment {
    social_account_id: string;
    replies: { social_account_id: string; state: string }[];
  }
  const replied = await api.get<{ data: Comment[] }>(`/workspaces/${ws}/inbox/comments?state=replied&limit=50`);
  for (const c of replied.data) {
    for (const r of c.replies.filter((x) => x.state === "sent")) expect(r.social_account_id).toBe(c.social_account_id);
  }
  await page.screenshot({ path: "test-results/phase2-inbox.png", fullPage: true });
});

// ---------------------------------------------------------------------- WhatsApp

interface Sent {
  id: string;
  to: string;
  type: string;
  body: Record<string, any>;
}
const textOf = (m: Sent): string =>
  m.body.text?.body ?? m.body.interactive?.body?.text ?? m.body.template?.components?.[0]?.parameters?.[0]?.text ?? "";
const choicesOf = (m: Sent): { id: string; title: string }[] => {
  const i = m.body.interactive;
  if (!i) return [];
  if (i.type === "button") return (i.action.buttons as { reply: { id: string; title: string } }[]).map((b) => b.reply);
  return (i.action.sections as { rows: { id: string; title: string }[] }[]).flatMap((s) => s.rows);
};

test("WhatsApp: pair from the Channels screen and approve from a WhatsApp card", async ({ page, request }) => {
  const api = await apiAs(request);
  const ws = await workspaceId(api);
  const channels = await api.get<{ whatsapp: { configured: boolean } }>(`/workspaces/${ws}/approval-channels`);
  test.skip(!channels.whatsapp.configured, "WhatsApp is not configured on this stack (see .claude/launch.json verify-*)");

  const chat = `1555${String(Math.floor(Math.random() * 1e7)).padStart(7, "0")}`;
  let n = 0;
  const inbound = async (message: Record<string, unknown>) => {
    const raw = JSON.stringify({
      object: "whatsapp_business_account",
      entry: [{ changes: [{ value: { messages: [{ from: chat, id: `wamid.e2e.${Date.now()}.${++n}`, ...message }] } }] }],
    });
    const sig = `sha256=${createHmac("sha256", WA_SECRET).update(raw).digest("hex")}`;
    const r = await request.post(`${BOTS}/api/v1/webhooks/whatsapp`, {
      data: raw,
      headers: { "content-type": "application/json", "x-hub-signature-256": sig },
    });
    expect(r.status(), await r.text()).toBe(200);
  };
  const sent = async () => ((await (await request.get(`${GRAPH}/_sent?to=${chat}`)).json()) as { data: Sent[] }).data;

  // A forged webhook is refused before anything is parsed.
  const forged = await request.post(`${BOTS}/api/v1/webhooks/whatsapp`, {
    data: "{}",
    headers: { "content-type": "application/json", "x-hub-signature-256": "sha256=" + "0".repeat(64) },
  });
  expect(forged.status()).toBe(401);

  // ---- pair from the web ----
  await signIn(page);
  await page.goto(`/w/${WORKSPACE}/channels`);
  await page.getByRole("radio", { name: "WhatsApp" }).check();
  await page.getByRole("button", { name: "Get a pairing code" }).click();
  const code = (await page.getByLabel("Pairing code").textContent())!.trim();
  expect(code).toMatch(/^MERA-/);
  await inbound({ type: "text", text: { body: `/pair ${code}` } });
  await expect.poll(async () => (await sent()).some((m) => textOf(m).includes("Paired with Mera")), { timeout: 60_000 }).toBe(true);
  cleanup.push(async () => {
    const { data } = await api.get<{ data: { id: string; channel_type: string; is_me: boolean }[] }>(`/workspaces/${ws}/approver-bindings`);
    for (const b of data.filter((x) => x.is_me && x.channel_type === "whatsapp")) {
      await api.post(`/workspaces/${ws}/approver-bindings/${b.id}/revoke`);
    }
  });

  // ---- a post for two personal accounts (no company page, so no PIN in the chat) ----
  interface Account {
    id: string;
    platform: string;
    account_type: string;
    status: string;
  }
  const { data: accounts } = await api.get<{ data: Account[] }>(`/workspaces/${ws}/social-accounts?limit=100`);
  const personal: Account[] = [];
  for (const a of accounts.filter((x) => x.account_type === "personal" && x.status === "active")) {
    if (!personal.some((p) => p.platform === a.platform)) personal.push(a);
  }
  expect(personal.length, "two active personal accounts in the seed").toBeGreaterThanOrEqual(2);
  const { data: groups } = await api.get<{ data: { id: string; name: string }[] }>(`/workspaces/${ws}/account-groups`);
  const groupId =
    groups.find((g) => g.name === "E2E WhatsApp")?.id ??
    (await api.post<{ id: string }>(`/workspaces/${ws}/account-groups`, {
      name: "E2E WhatsApp",
      social_account_ids: personal.slice(0, 2).map((a) => a.id),
    })).id;
  const title = `E2E WhatsApp ${new Date().toISOString()}`;
  const created = await api.post<PostSet>(`/workspaces/${ws}/post-sets`, {
    title,
    topic: `From the phone: ${fresh(4)}.`,
    targets: { account_group_ids: [groupId] },
    generate: true,
  });
  cleanup.push(() => cancelQueued(api, ws, created.id));
  await waitForDrafts(api, ws, created.id);
  // Cards for requests an earlier run left open may arrive on pairing: only a card sent
  // after this request counts, and this request is withdrawn afterwards if still open.
  const before = (await sent()).length;
  const req = await api.post<{ id: string }>(`/workspaces/${ws}/post-sets/${created.id}/request-approval`);
  cleanup.push(async () => {
    await api.post(`/workspaces/${ws}/approval-requests/${req.id}/cancel`, {});
  });

  // ---- the card arrives on WhatsApp; tap "Approve all" ----
  let card: Sent | undefined;
  await expect
    .poll(
      async () => {
        card = (await sent()).slice(before).find((m) => textOf(m).includes("Approval needed") && choicesOf(m).some((c) => c.title.startsWith("✅ Approve")));
        return Boolean(card);
      },
      { timeout: 60_000 },
    )
    .toBe(true);
  const approve = choicesOf(card!).find((c) => c.title.startsWith("✅ Approve"))!;
  const kind = card!.body.interactive.type === "button" ? "button_reply" : "list_reply";
  await inbound({ type: "interactive", context: { id: card!.id }, interactive: { type: kind, [kind]: approve } });
  await expect
    .poll(async () => (await api.get<{ state: string }>(`/workspaces/${ws}/approval-requests/${req.id}`)).state, { timeout: 60_000 })
    .toBe("approved");

  // The web shows the WhatsApp pairing and this month's message counts.
  await page.reload();
  await expect(page.getByText(/^WhatsApp/).first()).toBeVisible();
  await expect(page.getByText(/in-window messages/)).toBeVisible();
  await page.screenshot({ path: "test-results/phase2-whatsapp.png", fullPage: true });
});
