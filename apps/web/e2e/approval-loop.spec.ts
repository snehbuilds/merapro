import { expect, test, type APIRequestContext } from "@playwright/test";

/*
 * The Phase 1 loop in a real browser (P2-M0.7): one brief becomes a different
 * post per account in a group, a person approves them all in the web queue,
 * and the accounts publish staggered. Setup goes through the API so the test
 * is about the approval and publishing path, not the composer's layout.
 */

const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const EMAIL = process.env.E2E_EMAIL ?? "demo@example.com";
const PASSWORD = process.env.E2E_PASSWORD ?? "Mera demo password 2026!";
const WORKSPACE = process.env.E2E_WORKSPACE ?? "acme";
const GROUP = process.env.E2E_GROUP ?? "Tech News";
const PIN = "482916";

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

const VOCABULARY = (
  "harbor lantern orchard granite meadow compass ember quartz willow canyon falcon prism " +
  "glacier saffron tundra beacon cobalt juniper marble nectar orbit pebble quiver ripple " +
  "summit thistle umber velvet walnut yonder zephyr anchor bramble cinder dune fjord " +
  "garnet hollow inlet jasper kelp lagoon mosaic nimbus opal plume quill reef sable tide"
).split(" ");

function freshWords(n: number): string {
  const picked = new Set<string>();
  while (picked.size < n) picked.add(VOCABULARY[Math.floor(Math.random() * VOCABULARY.length)]!);
  return [...picked].join(", ");
}

async function apiAs(request: APIRequestContext) {
  const login = await request.post(`${API}/api/v1/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
  expect(login.ok(), await login.text()).toBe(true);
  const { access_token } = (await login.json()) as { access_token: string };
  const headers = { authorization: `Bearer ${access_token}` };
  return {
    async get<T>(path: string): Promise<T> {
      // uvicorn closes idle keep-alive connections after 5 s; a poll that reuses one at
      // that moment sees a reset, which is the transport, not the API.
      const r = await request
        .get(`${API}/api/v1${path}`, { headers })
        .catch(() => request.get(`${API}/api/v1${path}`, { headers }));
      expect(r.ok(), `${path}: ${await r.text()}`).toBe(true);
      return (await r.json()) as T;
    },
    async put<T>(path: string, data: unknown): Promise<T | null> {
      const r = await request.put(`${API}/api/v1${path}`, { headers, data });
      expect(r.ok(), `${path}: ${await r.text()}`).toBe(true);
      return r.status() === 204 ? null : ((await r.json()) as T);
    },
    async delete(path: string): Promise<void> {
      const r = await request.delete(`${API}/api/v1${path}`, { headers });
      expect(r.ok(), `${path}: ${await r.text()}`).toBe(true);
    },
    async post<T>(path: string, data: unknown): Promise<T> {
      const r = await request.post(`${API}/api/v1${path}`, { headers, data });
      expect(r.ok(), `${path}: ${await r.text()}`).toBe(true);
      return (await r.json()) as T;
    },
  };
}

// Slots this run leaves queued are cancelled afterwards: otherwise the next run's posts
// queue behind them (ten minutes per account) and the test slows down every time.
let cleanup: (() => Promise<void>) | null = null;
test.afterEach(async () => {
  await cleanup?.();
  cleanup = null;
});

interface Slot {
  id: string;
  post_set_id: string;
  scheduled_for: string;
  state: string;
}

test("a group post set is approved in the web queue and publishes staggered", async ({ page, request }) => {
  const api = await apiAs(request);
  const { data: workspaces } = await api.get<{ data: { id: string; slug: string }[] }>("/workspaces");
  const ws = workspaces.find((w) => w.slug === WORKSPACE);
  expect(ws, `workspace ${WORKSPACE} (run the seed)`).toBeTruthy();
  const { data: groups } = await api.get<{ data: { id: string; name: string }[] }>(`/workspaces/${ws!.id}/account-groups`);
  const group = groups.find((g) => g.name === GROUP);
  expect(group, `group ${GROUP}`).toBeTruthy();

  // The group has a company Page, so approving needs a second factor (P2-M2.5): the
  // demo user's approval PIN, set (or reset) here with their password as proof.
  await api.put("/auth/pin", { pin: PIN, proof: PASSWORD });

  const title = `E2E approval loop ${new Date().toISOString()}`;
  const created = await api.post<PostSet>(`/workspaces/${ws!.id}/post-sets`, {
    title,
    // A fresh brief every run: the duplicate guardrail rightly blocks a repeat of an
    // earlier run's published post.
    topic: `Field notes: ${freshWords(4)}.`,
    targets: { account_group_ids: [group!.id] },
    generate: true,
  });

  // Drafting runs on the worker; wait until every enabled account has its text.
  let set = created;
  await expect
    .poll(
      async () => {
        set = await api.get<PostSet>(`/workspaces/${ws!.id}/post-sets/${created.id}`);
        if (set.generation_status === "failed") throw new Error("drafting failed");
        return set.generation_status === "idle" && set.variants.filter((v) => v.enabled).every((v) => v.current_version);
      },
      { timeout: 90_000, intervals: [1_000, 2_000, 3_000] },
    )
    .toBe(true);
  const accounts = set.variants.filter((v) => v.enabled);
  expect(accounts.length).toBeGreaterThan(1);

  // A time is asked for: with none, the Scheduler agent (P2-M7) proposes each account's
  // best-scoring hour of the week from its history, which can be days away. An explicit
  // time is honoured (then quiet hours, caps and spacing still apply).
  await api.post(`/workspaces/${ws!.id}/post-sets/${created.id}/request-approval`, {
    scheduled_for: new Date(Date.now() + 3 * 60_000).toISOString(),
  });

  // ---- the browser part: sign in, find the card, approve every account ----
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/w\//);

  await page.goto(`/w/${WORKSPACE}/approvals`);
  const card = page.getByRole("region", { name: title });
  await expect(card).toBeVisible();
  for (const v of accounts) await expect(card.getByText(v.nickname, { exact: false }).first()).toBeVisible();
  await card.getByRole("button", { name: new RegExp(`^Approve all ${accounts.length}$`) }).click();
  const dialog = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("radio", { name: "PIN" }).click();
  await dialog.getByLabel("Approval PIN").fill(PIN);
  await dialog.getByRole("button", { name: "Confirm" }).click();
  // Decided requests leave the pending list.
  await expect(card).toBeHidden({ timeout: 30_000 });

  // Every account is placed on approval, minutes apart, from the requested time; when an
  // earlier run left posts queued on the same accounts, the per-account spacing pushes it
  // later, so wait for the set's own first slot.
  set = await api.get<PostSet>(`/workspaces/${ws!.id}/post-sets/${created.id}`);
  for (const v of set.variants.filter((x) => x.enabled)) {
    expect(["scheduled", "publishing", "published"], `${v.nickname} is ${v.state}`).toContain(v.state);
  }
  const { data: slots } = await api.get<{ data: Slot[] }>(`/workspaces/${ws!.id}/scheduled-posts`);
  cleanup = async () => {
    const { data: now } = await api.get<{ data: Slot[] }>(`/workspaces/${ws!.id}/scheduled-posts?state=queued`);
    for (const s of now.filter((x) => x.post_set_id === created.id)) {
      await api.delete(`/workspaces/${ws!.id}/scheduled-posts/${s.id}`);
    }
  };
  const mine = slots.filter((s) => s.post_set_id === created.id).map((s) => Date.parse(s.scheduled_for));
  expect(mine.length).toBe(accounts.length);
  expect(new Set(mine).size, "each account at its own time").toBe(mine.length);
  const firstDueIn = Math.max(0, Math.min(...mine) - Date.now());
  expect(firstDueIn, "first slot within the spacing window").toBeLessThan(11 * 60_000);
  await expect
    .poll(
      async () => {
        set = await api.get<PostSet>(`/workspaces/${ws!.id}/post-sets/${created.id}`);
        return set.variants.filter((v) => v.state === "published").length;
      },
      // The worker dispatches once a minute.
      { timeout: firstDueIn + 150_000, intervals: [4_000] },
    )
    .toBeGreaterThan(0);

  // The calendar shows each account's slot with its post text and state.
  await page.goto(`/w/${WORKSPACE}/calendar`);
  await expect(page.getByText(/Field notes:/).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /, Published\. Open to reschedule\.$/ }).first()).toBeVisible();
  await page.screenshot({ path: "test-results/approval-loop-calendar.png", fullPage: true });
});
