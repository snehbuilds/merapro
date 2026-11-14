// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/mcp" }));

const sdk = { approveServer: vi.fn(), decideCall: vi.fn(), registerServer: vi.fn(), updateTool: vi.fn(), disableServer: vi.fn(), importServers: vi.fn() };
vi.mock("@mera/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mera/sdk")>();
  const mocked = Object.fromEntries(Object.keys(sdk).map((k) => [k, (...a: unknown[]) => sdk[k as keyof typeof sdk](...a)]));
  return { ...actual, ...mocked };
});

const { RegistryPanel } = await import("@/components/mcp/registry-panel");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}

const tool = {
  id: "t1",
  name: "create_page",
  title: null,
  description: "Create a page.",
  risk_class: "write" as const,
  approval_policy: "always" as const,
  enabled: false,
  flag: null,
  flag_acknowledged: false,
  presence: "present",
  agents: [],
  exposed_name: "notion__create_page",
};
const server = {
  id: "s1",
  slug: "notion",
  name: "Notion",
  transport: "http" as const,
  url: "https://notion.example/mcp",
  package: null,
  state: "pending_review",
  protocol_era: "modern",
  auth_method: "none",
  has_credentials: false,
  egress_allowlist: [],
  last_discovered_at: "2026-11-10T00:00:00Z",
  last_error: null,
  tools: [tool, { ...tool, id: "t2", name: "helper", flag: "The description contains an instruction to the model." }],
};
const call = {
  id: "c1",
  server_id: "s1",
  tool_name: "create_page",
  arguments: { title: "Launch notes" },
  content_hash: "b".repeat(64),
  agent_key: "writer",
  state: "pending_approval",
  error: null,
  expires_at: "2026-11-10T10:30:00Z",
};
const ws = { id: "ws-1", timezone: "UTC" };

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("MCP registry (P2-M9)", () => {
  it("shows review state, flags, and approves", async () => {
    sdk.approveServer.mockReturnValue(ok({}));
    render(<RegistryPanel workspace={ws} servers={[server]} calls={[]} canManage canDecide />);
    expect(screen.getByText("pending review")).toBeTruthy();
    expect(screen.getByText(/instruction to the model/)).toBeTruthy();
    expect((screen.getByRole("checkbox", { name: "Enable create_page" }) as HTMLInputElement).checked).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(sdk.approveServer).toHaveBeenCalled());
    expect(router.refresh).toHaveBeenCalled();
  });

  it("shows the exact arguments of a waiting call and allows with its hash", async () => {
    sdk.decideCall.mockReturnValue(ok({}));
    render(<RegistryPanel workspace={ws} servers={[]} calls={[call]} canManage={false} canDecide />);
    const list = screen.getByRole("list", { name: "Tool calls waiting" });
    expect(within(list).getByLabelText("Exact arguments").textContent).toContain('"title": "Launch notes"');
    await userEvent.click(within(list).getByRole("button", { name: "Allow" }));
    await waitFor(() => expect(sdk.decideCall).toHaveBeenCalled());
    expect(sdk.decideCall.mock.calls[0]?.[0].body).toEqual({ allow: true, content_hash: "b".repeat(64) });
    expect(screen.queryByRole("button", { name: "Register for review" })).toBeNull();
  });

  it("registers a remote server for review", async () => {
    sdk.registerServer.mockReturnValue(ok({}, 201));
    render(<RegistryPanel workspace={ws} servers={[]} calls={[]} canManage canDecide />);
    await userEvent.type(screen.getByLabelText("Slug"), "notion");
    await userEvent.type(screen.getByLabelText("URL"), "https://notion.example/mcp");
    await userEvent.click(screen.getByRole("button", { name: "Register for review" }));
    await waitFor(() => expect(sdk.registerServer).toHaveBeenCalled());
    expect(sdk.registerServer.mock.calls[0]?.[0].body).toMatchObject({ slug: "notion", transport: "http", url: "https://notion.example/mcp", package: null });
  });
});
