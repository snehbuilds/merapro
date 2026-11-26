// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/composer" }));

const sdk = {
  listPlatforms: vi.fn(),
  estimateGeneration: vi.fn(),
  generateMedia: vi.fn(),
  getMediaJob: vi.fn(),
  setMediaLimits: vi.fn(),
};
vi.mock("@mera/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mera/sdk")>();
  const mocked = Object.fromEntries(Object.keys(sdk).map((k) => [k, (...a: unknown[]) => sdk[k as keyof typeof sdk](...a)]));
  return { ...actual, ...mocked };
});

const { MediaPanel, usd } = await import("@/components/composer/media-panel");
const { MediaSpendPanel } = await import("@/components/settings/media-spend-panel");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}

const estimate = {
  kind: "image",
  count: 1,
  cost_usd: "0.063",
  seconds: 15,
  request_ceiling_usd: "0.25",
  month_spent_usd: "0.12",
  monthly_budget_usd: "20.00",
  needs_second_factor: false,
  over_budget: false,
  provider: "fake",
};
const job = {
  id: "j1",
  kind: "image",
  state: "succeeded",
  prompt: "p",
  alt_text: "A lighthouse at dawn",
  provider: "fake",
  model: "fake-image-1",
  post_set_id: "ps1",
  post_variant_id: null,
  batch_id: null,
  slot: 0,
  estimated_cost_usd: "0.063",
  actual_cost_usd: "0.063",
  attempt_count: 1,
  error: null,
  result_asset_id: "asset-9",
  created_at: "2026-11-10T10:00:00Z",
  finished_at: "2026-11-10T10:00:02Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  sdk.listPlatforms.mockReturnValue(
    ok({ data: [{ platform: "linkedin_member", max_media: 9, requires_media: false, media_aspects: [{ key: "1.91x1", width: 1200, height: 627 }] }] }),
  );
  sdk.estimateGeneration.mockReturnValue(ok(estimate));
});
afterEach(cleanup);

describe("Composer: generate images (P2-M5)", () => {
  it("shows the cost first, plans from the post without a prompt, and adds the result to pick", async () => {
    sdk.generateMedia.mockReturnValue(ok({ data: [job] }, 202));
    const onSave = vi.fn().mockResolvedValue({ ok: true });
    render(<MediaPanel workspaceId="ws-1" platform="linkedin_member" media={[]} contentItemId={null} variantId="v1" editable onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByRole("button", { name: "Generate · $0.063" })).toBeTruthy();
    expect(sdk.estimateGeneration.mock.calls[0]?.[0].body).toMatchObject({ aspect: "landscape", count: 1 });
    expect(screen.getByText(/\$0\.12 of \$20\.00 used this month/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Generate · $0.063" }));
    await waitFor(() => expect(sdk.generateMedia).toHaveBeenCalled());
    expect(sdk.generateMedia.mock.calls[0]?.[0].body).toMatchObject({ prompt: null, post_variant_id: "v1", attach: false });
    // The generated image joins the list with the agent's alt text, ready to save.
    expect(await screen.findByDisplayValue("A lighthouse at dawn")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Save media" }));
    expect(onSave.mock.calls[0]?.[0]).toEqual([{ asset_id: "asset-9", aspect: "1.91x1", alt_text: "A lighthouse at dawn" }]);
  });

  it("refuses to start over the monthly budget", async () => {
    sdk.estimateGeneration.mockReturnValue(ok({ ...estimate, over_budget: true }));
    render(<MediaPanel workspaceId="ws-1" platform="linkedin_member" media={[]} contentItemId={null} variantId="v1" editable onSave={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(await screen.findByText(/would go over the month's budget/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Generate · $0.063" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("formats small amounts to the tenth of a cent", () => {
    expect(usd("0.042")).toBe("$0.042");
    expect(usd("20")).toBe("$20.00");
  });
});

describe("Settings: media spend (P2-M5)", () => {
  it("shows the month against the budget and saves limits", async () => {
    sdk.setMediaLimits.mockReturnValue(ok({}));
    const spend = {
      month_start: "2026-11-01T00:00:00Z",
      month_spent_usd: "18.50",
      monthly_budget_usd: "20.00",
      request_ceiling_usd: "0.25",
      jobs: { succeeded: 40, failed: 2 },
      provider: "openai",
      video_enabled: false,
    };
    render(<MediaSpendPanel workspaceId="ws-1" spend={spend} canEdit />);
    expect(screen.getByRole("meter", { name: "Media budget used this month" }).getAttribute("aria-valuenow")).toBe("93");
    expect(screen.getByText(/40 done · 2 failed/)).toBeTruthy();
    const budget = screen.getByLabelText("Monthly budget (USD)");
    await userEvent.clear(budget);
    await userEvent.type(budget, "50");
    await userEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(sdk.setMediaLimits).toHaveBeenCalled());
    expect(sdk.setMediaLimits.mock.calls[0]?.[0].body).toEqual({ request_ceiling_usd: "0.25", monthly_budget_usd: "50" });
  });
});
