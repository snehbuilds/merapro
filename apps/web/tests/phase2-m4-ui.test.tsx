// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/channels" }));

const sdk = { mintPairingCode: vi.fn(), setWhatsapp: vi.fn(), testWhatsapp: vi.fn() };
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

const { ChannelsScreen } = await import("@/components/channels/channels-screen");

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
const telegram = { configured: true, sandbox: false, bot_username: "mera_bot", mode: "webhook", enabled: true, last_test_at: null, last_test_result: null };
const whatsapp = {
  available: true,
  configured: true,
  display_number: "4915112345678",
  enabled: true,
  last_test_at: null,
  last_test_result: null,
  usage: { template: 12, session: 40, cap: 1000 },
};
const binding = {
  id: "b1",
  user_id: "u1",
  user_name: "Alex",
  channel_type: "whatsapp",
  external_label: "+49 151",
  status: "active",
  verified_at: "2026-11-01T10:00:00Z",
  is_me: true,
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("Channels: WhatsApp (P2-M4)", () => {
  it("shows the number, switch and the month's template budget", () => {
    render(shell(<ChannelsScreen workspace={ws} status={{ telegram, whatsapp }} bindings={[binding]} canManage />));
    expect(screen.getByText("+4915112345678")).toBeTruthy();
    expect(screen.getByText("12 of 1000 · 40 in-window messages")).toBeTruthy();
    expect(screen.getByRole("switch", { name: /WhatsApp/ })).toBeTruthy();
    // The paired chat is labelled with its channel.
    expect(screen.getByText(/WhatsApp · \+49 151/)).toBeTruthy();
  });

  it("mints a WhatsApp code when WhatsApp is picked", async () => {
    sdk.mintPairingCode.mockReturnValue(
      ok({ code: "MERA-ABCD-EFGH", command: "/pair MERA-ABCD-EFGH", deep_link: "https://wa.me/4915112345678?text=%2Fpair%20MERA-ABCD-EFGH", expires_at: "2026-11-10T05:00:00Z" }, 201),
    );
    render(shell(<ChannelsScreen workspace={ws} status={{ telegram, whatsapp }} bindings={[]} canManage />));
    await userEvent.click(screen.getByRole("radio", { name: "WhatsApp" }));
    await userEvent.click(screen.getByRole("button", { name: "Get a pairing code" }));
    await waitFor(() => expect(sdk.mintPairingCode).toHaveBeenCalled());
    expect(sdk.mintPairingCode.mock.calls[0]?.[0].body).toEqual({ channel_type: "whatsapp" });
    expect(await screen.findByRole("link", { name: "open WhatsApp and pair in one tap" })).toBeTruthy();
  });

  it("says Not set up, without a channel choice, when the deployment has no WhatsApp", () => {
    const off = { ...whatsapp, configured: false, enabled: false, usage: null, display_number: null };
    render(shell(<ChannelsScreen workspace={ws} status={{ telegram, whatsapp: off }} bindings={[]} canManage />));
    expect(screen.getByText("Not set up")).toBeTruthy();
    expect(screen.queryByRole("radio", { name: "WhatsApp" })).toBeNull();
  });
});
