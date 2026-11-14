// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/mcp" }));

const sdk = { authorize: vi.fn(), revokeClient: vi.fn(), confirmPending: vi.fn(), declinePending: vi.fn() };
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

const { ConsentForm } = await import("@/components/mcp/consent-form");
const { ConfirmAction } = await import("@/components/mcp/confirm-action");
const { McpScreen } = await import("@/components/mcp/mcp-screen");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}

const ws = { id: "ws-1", slug: "acme", timezone: "UTC", archived: false };
const shellWs = { ...ws, name: "Acme", mark: "AC", meta: "Admin · UTC", role: "admin" };
const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };
const shell = (c: ReactNode) => (
  <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
    {c}
  </ShellProvider>
);
const client = { client_id: "https://claude.example/client.json", client_name: "Claude", redirect_uris: ["http://127.0.0.1/callback"] };
const request = {
  client_id: client.client_id,
  redirect_uri: "http://127.0.0.1:33418/callback",
  code_challenge: "c".repeat(43),
  scope: "drafts:read drafts:write",
  resource: "https://mcp.example/mcp",
  state: "s1",
};

const assign = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(window, "location", { value: { ...window.location, assign }, writable: true });
});
afterEach(cleanup);

describe("OAuth consent for MCP clients (P2-M8)", () => {
  it("shows who asks and what for, then sends the person back with a code", async () => {
    sdk.authorize.mockReturnValue(ok({ redirect_to: "http://127.0.0.1:33418/callback?code=abc&state=s1" }));
    render(<ConsentForm client={client} request={request} workspaces={[{ id: "ws-1", name: "Acme" }]} issuer="https://api.example" />);
    expect(screen.getByRole("heading", { name: "Connect Claude" })).toBeTruthy();
    expect(screen.getByText(/Create drafts and send them for approval/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Allow" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("http://127.0.0.1:33418/callback?code=abc&state=s1"));
    expect(sdk.authorize.mock.calls[0]?.[0].body).toMatchObject({ workspace_id: "ws-1", code_challenge_method: "S256", resource: request.resource });
  });

  it("refuses to continue to an unregistered redirect", () => {
    render(<ConsentForm client={client} request={{ ...request, redirect_uri: "https://evil.example/cb" }} workspaces={[{ id: "ws-1", name: "Acme" }]} issuer="x" />);
    expect(screen.getByRole("alert").textContent).toMatch(/did not register/);
    expect((screen.getByRole("button", { name: "Allow" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("denies with access_denied, the state and the issuer", async () => {
    render(<ConsentForm client={client} request={request} workspaces={[{ id: "ws-1", name: "Acme" }]} issuer="https://api.example" />);
    await userEvent.click(screen.getByRole("button", { name: "Deny" }));
    const url = new URL(assign.mock.calls[0]?.[0] as string);
    expect(url.searchParams.get("error")).toBe("access_denied");
    expect(url.searchParams.get("state")).toBe("s1");
    expect(url.searchParams.get("iss")).toBe("https://api.example");
  });
});

describe("Confirm page and MCP screen (P2-M8)", () => {
  it("confirms a pending action on the web", async () => {
    sdk.confirmPending.mockReturnValue(ok({ state: "done" }));
    const pending = { id: "p1", action: "comment_delete", tool: "delete_comment", arguments: {}, summary: "Delete a comment", state: "pending", result: null, expires_at: "2026-11-10T10:15:00Z", confirm_url: null };
    render(shell(<ConfirmAction workspace={{ id: "ws-1", timezone: "UTC" }} pending={pending} />));
    await userEvent.click(screen.getByRole("button", { name: "Confirm with second factor" }));
    expect(await screen.findByText(/Go back to your MCP client/)).toBeTruthy();
  });

  it("lists connected clients and disconnects one", async () => {
    sdk.revokeClient.mockReturnValue(Promise.resolve({ data: undefined, response: new Response(null, { status: 204 }) }));
    const grant = { id: "g1", user_id: "u1", client_id: client.client_id, client_name: "Claude", scopes: ["drafts:read"], created_at: "2026-11-01T00:00:00Z", last_used_at: null, expires_at: "2026-11-10T00:00:00Z" };
    render(shell(<McpScreen workspace={ws} clients={[grant]} calls={[]} endpoint="https://mcp.example/mcp" canManage />));
    expect(screen.getByText("https://mcp.example/mcp")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Disconnect Claude" }));
    await waitFor(() => expect(sdk.revokeClient).toHaveBeenCalled());
    expect(router.refresh).toHaveBeenCalled();
  });
});
