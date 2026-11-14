// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const sdk = { createInvitation: vi.fn(), revokeInvitation: vi.fn() };
vi.mock("@mera/sdk", () => ({ ...sdk, createMeraClient: () => ({}) }));

const { InvitationsPanel } = await import("@/components/team/invitations-panel");

function created(emailSent: boolean) {
  return {
    data: {
      invitation: { id: "i1", email: "new@example.com", role: "editor", expires_at: "2026-11-16T00:00:00Z" },
      token: "mera_inv_x",
      accept_url: "https://mera.example/invite/mera_inv_x",
      email_sent: emailSent,
    },
    response: new Response(null, { status: 201 }),
  };
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

async function invite() {
  render(<InvitationsPanel workspaceId="w1" invitations={[]} tz="America/Los_Angeles" archived={false} />);
  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "new@example.com");
  await userEvent.click(screen.getByRole("button", { name: "Invite" }));
}

describe("Team → invitations", () => {
  it("says the invitation was emailed when the server sent it", async () => {
    sdk.createInvitation.mockResolvedValue(created(true));
    await invite();
    expect(await screen.findByText(/We emailed them this link/)).toBeVisible();
    expect(router.refresh).toHaveBeenCalled();
  });

  it("asks the inviter to share the link when no email went out", async () => {
    sdk.createInvitation.mockResolvedValue(created(false));
    await invite();
    expect(await screen.findByText(/share this link with them yourself/)).toBeVisible();
  });
});
