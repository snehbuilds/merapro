// @vitest-environment jsdom
import type { AccountOut, GroupOut } from "@mera/sdk";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";
import type { Agent, AgentRun, AgentStep, CostReport, PostSet, Voice } from "@/lib/api/models";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/voice" }));

const sdk = {
  createVoice: vi.fn(),
  updateVoice: vi.fn(),
  deleteVoice: vi.fn(),
  sampleVoice: vi.fn(),
  getAgentRun: vi.fn(),
  getPostSet: vi.fn(),
};
vi.mock("@mera/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mera/sdk")>();
  const mocked = Object.fromEntries(Object.keys(sdk).map((k) => [k, (...a: unknown[]) => sdk[k as keyof typeof sdk](...a)]));
  return { ...actual, ...mocked };
});

window.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as typeof window.matchMedia;

const { VoiceScreen } = await import("@/components/voice/voice-screen");
const { AgentsScreen } = await import("@/components/agents/agents-screen");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}
function fail(status: number, code: string, message: string, field?: string) {
  return Promise.resolve({
    error: { error: { code, message, field_errors: field ? [{ field, code, message }] : [] } },
    response: new Response(null, { status }),
  });
}

const ws = { id: "ws-1", slug: "acme", name: "Acme", timezone: "UTC", archived: false };
const shellWs = { ...ws, mark: "AC", meta: "Editor · UTC", role: "editor" };
const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };

function shell(children: ReactNode) {
  return (
    <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
      {children}
    </ShellProvider>
  );
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

/* ---- Brand Voice ---- */

function voice(id: string, over: Partial<Voice> = {}): Voice {
  return {
    id,
    name: id,
    scope_type: "workspace",
    scope_id: null,
    scope_name: "Acme",
    is_default: false,
    tone: null,
    target_audience: null,
    emoji_policy: "none",
    cta_style: null,
    banned_words: [],
    example_posts: [],
    learned_preferences: [],
    about: null,
    story_bank: [],
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}

const corporate = voice("v-corp", {
  name: "Acme Corporate",
  is_default: true,
  tone: "Measured, institutional.",
  banned_words: ["game changer", "excited to share"],
  example_posts: ["We shipped the thing."],
});
const technical = voice("v-tech", {
  name: "Technical",
  scope_type: "account_group",
  scope_id: "grp-tech",
  scope_name: "Tech News",
  tone: "Plain, specific, no hype.",
});
const personal = voice("v-personal", {
  name: "Personal",
  scope_type: "social_account",
  scope_id: "a-li",
  scope_name: "Work LinkedIn",
  tone: "Direct, first person.",
});

const groups = [{ id: "grp-tech", name: "Tech News", members: [], member_count: 0 } as unknown as GroupOut];
const accounts = [{ id: "a-li", nickname: "Work LinkedIn", status: "active" } as unknown as AccountOut];

function renderVoice(props: Partial<Parameters<typeof VoiceScreen>[0]> = {}) {
  return render(
    shell(<VoiceScreen workspace={ws} voices={[personal, technical, corporate]} groups={groups} accounts={accounts} canManage {...props} />),
  );
}

describe("Brand Voice", () => {
  it("lists profiles by scope, marks the default and opens the workspace default first", () => {
    renderVoice();
    expect(screen.getByText("3 profiles")).toBeInTheDocument();
    const sections = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(sections.slice(0, 3)).toEqual(["Workspace", "Groups", "Accounts"]);
    const corp = screen.getByRole("button", { name: /Acme Corporate/ });
    expect(corp).toHaveAttribute("aria-current", "true");
    expect(within(corp).getByText("Default")).toBeInTheDocument();
    expect(within(corp).getByText("Default · Measured, institutional")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Personal/ })).toHaveTextContent("Work LinkedIn · Direct, first person");
    expect(screen.getByText("Applies to the whole workspace")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Banned words and phrases" })).toHaveValue("game changer, excited to share");
    expect(screen.getByRole("textbox", { name: "Example 1" })).toHaveValue("We shipped the thing.");
    expect(screen.getByText("Nothing learned yet")).toBeInTheDocument();
  });

  it("saves only what changed", async () => {
    const u = userEvent.setup();
    sdk.updateVoice.mockReturnValue(ok({ ...corporate, tone: "Calm.", updated_at: "2026-11-08T00:00:00Z" }));
    renderVoice();
    const tone = screen.getByRole("textbox", { name: "Tone" });
    await u.clear(tone);
    await u.type(tone, "Calm.");
    await u.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdk.updateVoice).toHaveBeenCalledTimes(1));
    expect(sdk.updateVoice.mock.calls[0]![0]).toMatchObject({ path: { voice_id: "v-corp" }, body: { tone: "Calm." } });
    expect(Object.keys(sdk.updateVoice.mock.calls[0]![0].body)).toEqual(["tone"]);
    expect(await screen.findByText("Saved Acme Corporate.")).toBeInTheDocument();
  });

  it("creates a voice at a group's scope", async () => {
    const u = userEvent.setup();
    sdk.createVoice.mockReturnValue(ok(voice("v-new", { name: "Launch", scope_type: "account_group", scope_id: "grp-tech", scope_name: "Tech News" }), 201));
    renderVoice();
    await u.click(screen.getByRole("button", { name: "New profile" }));
    const form = screen.getByRole("form", { name: "New profile" });
    await u.click(screen.getByRole("button", { name: "Create profile" }));
    expect(within(form).getByText("Name the voice.")).toBeInTheDocument();
    await u.type(within(form).getByRole("textbox", { name: "Name" }), "Launch");
    await u.selectOptions(within(form).getByRole("combobox", { name: /Scope/ }), "account_group");
    await u.click(screen.getByRole("button", { name: "Create profile" }));
    expect(within(form).getByText("Pick a group.")).toBeInTheDocument();
    await u.selectOptions(within(form).getByRole("combobox", { name: "Group" }), "grp-tech");
    await u.click(within(form).getByRole("button", { name: "Add example" }));
    await u.type(within(form).getByRole("textbox", { name: "Example 1" }), "Launch day.");
    await u.click(screen.getByRole("button", { name: "Create profile" }));
    await waitFor(() => expect(sdk.createVoice).toHaveBeenCalledTimes(1));
    expect(sdk.createVoice.mock.calls[0]![0].body).toMatchObject({
      name: "Launch",
      scope_type: "account_group",
      scope_id: "grp-tech",
      example_posts: ["Launch day."],
      is_default: false,
    });
    expect(await screen.findByText("Created Launch.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Launch/ })).toHaveAttribute("aria-current", "true");
  });

  it("explains a 403 for a scope the editor doesn't cover", async () => {
    const u = userEvent.setup();
    sdk.createVoice.mockReturnValue(fail(403, "forbidden", "Forbidden."));
    renderVoice();
    await u.click(screen.getByRole("button", { name: "New profile" }));
    await u.type(screen.getByRole("textbox", { name: "Name" }), "Mine");
    await u.click(screen.getByRole("button", { name: "Create profile" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You can't change voices at that scope.");
  });

  it("duplicates, sets a default and deletes", async () => {
    const u = userEvent.setup();
    sdk.createVoice.mockReturnValue(ok(voice("v-copy", { ...technical, id: "v-copy", name: "Technical (copy)" }), 201));
    sdk.updateVoice.mockReturnValue(ok({ ...technical, is_default: true }));
    sdk.deleteVoice.mockReturnValue(Promise.resolve({ data: undefined, response: new Response(null, { status: 204 }) }));
    renderVoice();
    await u.click(screen.getByRole("button", { name: /Technical/ }));

    await u.click(screen.getByRole("button", { name: "Set as default" }));
    await waitFor(() => expect(sdk.updateVoice).toHaveBeenCalledWith(expect.objectContaining({ body: { is_default: true } })));
    expect(await screen.findByText("Technical is now the default for Tech News.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as default" })).toBeNull();

    await u.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(sdk.createVoice).toHaveBeenCalledTimes(1));
    expect(sdk.createVoice.mock.calls[0]![0].body).toMatchObject({
      name: "Technical (copy)",
      scope_type: "account_group",
      scope_id: "grp-tech",
      is_default: false,
    });
    expect(await screen.findByText("Duplicated Technical as Technical (copy).")).toBeInTheDocument();

    await u.click(screen.getByRole("button", { name: "Delete" }));
    await u.click(screen.getByRole("button", { name: "Delete voice" }));
    await waitFor(() => expect(sdk.deleteVoice).toHaveBeenCalledWith(expect.objectContaining({ path: { workspace_id: "ws-1", voice_id: "v-copy" } })));
    expect(screen.queryByRole("button", { name: /Technical \(copy\)/ })).toBeNull();
  });

  it("drafts a sample with the voice", async () => {
    const u = userEvent.setup();
    sdk.sampleVoice.mockReturnValue(
      ok({ body: "Postgres 18 is out. Async I/O is the headline.", angle: "Release news", self_critique: "Could name the version.", voice_name: "Acme Corporate" }),
    );
    renderVoice();
    await u.click(screen.getByRole("button", { name: "Generate sample" }));
    expect(screen.getByText("Say what the sample should be about.")).toBeInTheDocument();
    await u.type(screen.getByRole("textbox", { name: "Draft a sample about" }), "Postgres 18 async I/O");
    await u.selectOptions(screen.getByRole("combobox", { name: "Platform" }), "threads");
    await u.click(screen.getByRole("button", { name: "Generate sample" }));
    expect(await screen.findByText("Postgres 18 is out. Async I/O is the headline.")).toBeInTheDocument();
    expect(sdk.sampleVoice.mock.calls[0]![0]).toMatchObject({ path: { voice_id: "v-corp" }, body: { topic: "Postgres 18 async I/O", platform: "threads" } });
    expect(screen.getByText("Release news")).toBeInTheDocument();
    expect(screen.getByText("Could name the version.")).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: "Threads preview" })).toBeInTheDocument();
  });

  it("asks to wait when samples are rate limited", async () => {
    const u = userEvent.setup();
    sdk.sampleVoice.mockReturnValue(
      Promise.resolve({
        error: { error: { code: "rate_limited", message: "Slow down.", field_errors: [] } },
        response: new Response(null, { status: 429 }),
      }),
    );
    renderVoice();
    await u.type(screen.getByRole("textbox", { name: "Draft a sample about" }), "Anything");
    await u.click(screen.getByRole("button", { name: "Generate sample" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts, try again in a moment.");
  });

  it("is read-only for viewers", () => {
    renderVoice({ canManage: false });
    expect(screen.queryByRole("button", { name: "New profile" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Tone" })).toBeDisabled();
    expect(screen.getByText("Editors and up change voices.")).toBeInTheDocument();
  });

  it("opens the create form when there are no voices", () => {
    renderVoice({ voices: [] });
    expect(screen.getByText("No voices yet")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "New profile" })).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = renderVoice();
    expect((await axe.run(container)).violations).toEqual([]);
  });
});

/* ---- Agents ---- */

const agents: Agent[] = [
  { agent_key: "writer", name: "Writer", model: "anthropic/claude-sonnet-4-5", fallback_models: ["openai/gpt-5"], temperature: 0.8, cost_30d_usd: "18.40000000", editable: false, default_model: null, choices: [] },
  { agent_key: "hashtag", name: "Hashtag", model: "openai/gpt-5-mini", fallback_models: [], temperature: 0.2, cost_30d_usd: "0.00000000", editable: false, default_model: null, choices: [] },
];

function step(seq: number, over: Partial<AgentStep> = {}): AgentStep {
  return {
    id: `st-${seq}`,
    seq,
    agent_key: "writer",
    step_type: "llm_call",
    model: "anthropic/claude-sonnet-4-5",
    fallback_from: null,
    tokens_in: 1200,
    tokens_out: 300,
    cost_usd: "0.03100000",
    latency_ms: 2100,
    state: "succeeded",
    error: null,
    post_variant_id: null,
    price_source: null,
    input_redacted: {},
    output: {},
    started_at: "2026-11-08T09:00:00Z",
    ...over,
  };
}

function run(id: string, over: Partial<AgentRun> = {}): AgentRun {
  return {
    id,
    workflow_key: "draft_from_content",
    state: "succeeded",
    trigger: "manual",
    subject_type: "post_set",
    subject_id: "ps-1",
    started_at: "2026-11-08T09:00:00Z",
    finished_at: "2026-11-08T09:00:38.100Z",
    cost_usd: "0.16230000",
    tokens_in: 9000,
    tokens_out: 2000,
    step_count: 3,
    error: null,
    steps: [],
    ...over,
  };
}

const latest = run("0193a4f2-aaaa-7bbb-8ccc-000000000001", {
  steps: [
    step(1, { agent_key: "research", model: "anthropic/claude-haiku-4-5", cost_usd: "0.00080000" }),
    step(2, { post_variant_id: "v-li" }),
    step(3, { post_variant_id: "v-xtech", model: "openai/gpt-5", fallback_from: "anthropic/claude-sonnet-4-5", cost_usd: "0.02650000" }),
    step(4, { agent_key: "editor", step_type: "embedding", model: null, cost_usd: "0", tokens_in: 0, tokens_out: 0, latency_ms: 40 }),
  ],
});
const sample = run("0193a4f2-bbbb-7bbb-8ccc-000000000002", {
  workflow_key: "voice_sample",
  subject_type: "brand_voice",
  subject_id: "v-corp",
  state: "failed",
  error: "Writer timed out.",
  cost_usd: "0",
  step_count: 1,
});

const costs: CostReport = {
  since: "2026-10-09T00:00:00Z",
  until: "2026-11-08T00:00:00Z",
  total_usd: "31.20000000",
  by_agent: [
    { key: "hashtag", label: "Hashtag", calls: 12, cost_usd: "0", tokens_in: 0, tokens_out: 0 },
    { key: "writer", label: "Writer", calls: 120, cost_usd: "18.40000000", tokens_in: 1, tokens_out: 1 },
  ],
  by_model: [{ key: "m", label: "anthropic/claude-sonnet-4-5", calls: 120, cost_usd: "18.40000000", tokens_in: 1, tokens_out: 1 }],
  by_workflow: [{ key: "w", label: "draft_from_content", calls: 120, cost_usd: "18.40000000", tokens_in: 1, tokens_out: 1 }],
  by_account: [{ key: "a-xtech", label: "X @tech", calls: 40, cost_usd: "0.06120000", tokens_in: 1, tokens_out: 1 }],
};

function renderAgents(props: Partial<Parameters<typeof AgentsScreen>[0]> = {}) {
  return render(shell(<AgentsScreen workspace={ws} agents={agents} runs={[latest, sample]} initialRun={latest} costs={costs} {...props} />));
}

describe("Agents", () => {
  beforeEach(() => {
    sdk.getPostSet.mockReturnValue(
      ok({ id: "ps-1", variants: [{ id: "v-li", nickname: "Work LinkedIn" }, { id: "v-xtech", nickname: "X @tech" }] } as unknown as PostSet),
    );
  });

  it("shows the run's steps with the account each served, the fallback and real costs", async () => {
    renderAgents();
    expect(screen.getByText("2 agents · $31.20 in 30 days")).toBeInTheDocument();
    const detail = screen.getByRole("region", { name: "Run 0193A4F2" });
    expect(within(detail).getByText("draft_from_content")).toBeInTheDocument();
    expect(within(detail).getByText("Succeeded")).toBeInTheDocument();
    expect(within(detail).getByText("38.1s")).toBeInTheDocument();
    expect(within(detail).getByText("$0.1623")).toBeInTheDocument();
    const steps = within(detail).getByRole("list", { name: "Steps of run 0193A4F2" });
    await waitFor(() => expect(within(steps).getAllByText(/→ Work LinkedIn/)).not.toHaveLength(0));
    const rows = within(steps).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("research");
    expect(rows[0]).toHaveTextContent("$0.0008");
    expect(rows[2]).toHaveTextContent("→ X @tech · openai/gpt-5 · model call · fell back from anthropic/claude-sonnet-4-5");
    expect(rows[2]).toHaveAttribute("data-tone", "warn");
    expect(rows[3]).toHaveTextContent("$0");
    expect(within(detail).getByRole("link", { name: "Open the post set" })).toHaveAttribute("href", "/w/acme/composer?set=ps-1");
    expect(sdk.getPostSet).toHaveBeenCalledWith(expect.objectContaining({ path: { workspace_id: "ws-1", set_id: "ps-1" } }));
  });

  it("shows an orchestration's run graph with each child's steps and cost (P2-M7.4)", () => {
    const child = run("0193a4f2-cccc-7bbb-8ccc-000000000003", {
      cost_usd: "0.04000000",
      steps: [step(1, { agent_key: "writer", cost_usd: "0.03000000" })],
    });
    const orch = run("0193a4f2-dddd-7bbb-8ccc-000000000004", {
      workflow_key: "orchestration",
      state: "failed",
      cost_usd: "0",
      children: [child],
    } as Partial<AgentRun>);
    renderAgents({ runs: [orch], initialRun: orch, canManage: true });
    const graph = screen.getByRole("region", { name: "Run graph" });
    expect(within(graph).getByText("draft_from_content")).toBeInTheDocument();
    expect(within(graph).getByText("writer")).toBeInTheDocument();
    expect(within(graph).getAllByText(/^\$0\.03/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Retry from step" })).toBeNull();
  });

  it("lists the agent configuration read-only", () => {
    renderAgents();
    const table = screen.getByRole("table", { name: "Agents, their models and 30-day cost" });
    const writer = within(table).getByRole("row", { name: /Writer/ });
    expect(writer).toHaveTextContent("anthropic/claude-sonnet-4-5");
    expect(writer).toHaveTextContent("openai/gpt-5");
    expect(writer).toHaveTextContent("0.8");
    expect(writer).toHaveTextContent("$18.40");
    expect(within(table).getByRole("row", { name: /Hashtag/ })).toHaveTextContent("none");
    expect(within(table).getByRole("row", { name: /Hashtag/ })).toHaveTextContent("$0");
    expect(screen.getByText(/Editing models arrives with agent definitions next sprint/)).toBeInTheDocument();
  });

  it("breaks cost down by agent, model, workflow and account", () => {
    renderAgents();
    const cost = screen.getByRole("region", { name: "Cost" });
    expect(within(cost).getByText("$31.20")).toBeInTheDocument();
    const byAgent = within(cost).getByRole("region", { name: "By agent" });
    expect(within(byAgent).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Writer120 calls$18.40", "Hashtag12 calls$0"]);
    expect(within(within(cost).getByRole("region", { name: "By account" })).getByText("X @tech")).toBeInTheDocument();
    expect(within(cost).getByText("$0.0612")).toBeInTheDocument();
  });

  it("opens another run from the list", async () => {
    const u = userEvent.setup();
    sdk.getAgentRun.mockReturnValue(ok({ ...sample, steps: [step(1, { state: "failed", error: "timeout: Writer timed out", cost_usd: "0" })] }));
    renderAgents();
    const list = screen.getByRole("list", { name: "Recent runs" });
    await u.click(within(list).getByRole("button", { name: /voice_sample/ }));
    const detail = await screen.findByRole("region", { name: "Run 0193A4F2" });
    await waitFor(() => expect(within(detail).getByText("voice_sample")).toBeInTheDocument());
    expect(within(detail).getByText("Writer timed out.")).toBeInTheDocument();
    expect(within(detail).getByRole("listitem")).toHaveAttribute("data-tone", "danger");
    expect(within(list).getByRole("button", { name: /voice_sample/ })).toHaveAttribute("aria-current", "true");
  });

  it("explains why a scoped member sees no runs or costs", () => {
    renderAgents({ runs: null, initialRun: null, costs: null, scoped: true });
    expect(screen.getByText("Runs aren't shown at your scope")).toBeInTheDocument();
    expect(screen.getByText("Not shown at your scope.")).toBeInTheDocument();
    expect(screen.getByText("2 agents")).toBeInTheDocument();
    const writer = screen.getByRole("row", { name: /Writer/ });
    expect(writer).not.toHaveTextContent("$");
    expect(within(writer).getByText("Not shown at your scope")).toBeInTheDocument();
  });

  it("says so when there are no runs yet", () => {
    renderAgents({ runs: [], initialRun: null });
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = renderAgents();
    await waitFor(() => expect(screen.getAllByText(/→ Work LinkedIn/)).not.toHaveLength(0));
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
