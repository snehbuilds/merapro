// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const sdk = {
  acceptInvitation: vi.fn(),
  listWorkspaces: vi.fn(),
  requestEmailVerification: vi.fn(),
  verifyEmail: vi.fn(),
};
vi.mock("@mera/sdk", () => ({ ...sdk, createMeraClient: () => ({}) }));

const { AcceptInvitation } = await import("@/components/auth/accept-invitation");
const { VerifyEmail } = await import("@/components/auth/verify-email");

function fail(status: number, code: string) {
  return { error: { error: { code, message: code } }, response: new Response(null, { status }) };
}
function ok<T>(data: T, status = 200) {
  return { data, response: new Response(null, { status }) };
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("accepting an invitation with an unverified email", () => {
  it("asks for verification and shows the sandbox verify link", async () => {
    sdk.acceptInvitation.mockResolvedValue(fail(403, "email_unverified"));
    sdk.requestEmailVerification.mockResolvedValue(ok({ sent: true, verify_url: "http://localhost:3000/verify-email/tok-123" }));
    render(<AcceptInvitation token="inv-token-123" userName="Alex" userEmail="a@b.c" />);

    await userEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
    expect(await screen.findByText("Verify your email address first.")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Send verification link" }));
    expect(await screen.findByRole("link", { name: "Open the verification link" })).toHaveAttribute(
      "href",
      "http://localhost:3000/verify-email/tok-123",
    );
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("says when email delivery isn't configured (503)", async () => {
    sdk.acceptInvitation.mockResolvedValue(fail(403, "email_unverified"));
    sdk.requestEmailVerification.mockResolvedValue(fail(503, "email_delivery_unavailable"));
    render(<AcceptInvitation token="inv-token-123" userName="Alex" userEmail="a@b.c" />);
    await userEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send verification link" }));
    expect(await screen.findByText(/Email delivery isn't configured on this server/)).toBeVisible();
  });

  it("says check your inbox when the link was emailed, and explains a refused send", async () => {
    sdk.acceptInvitation.mockResolvedValue(fail(403, "email_unverified"));
    sdk.requestEmailVerification.mockResolvedValueOnce(ok({ sent: true, verify_url: null }));
    sdk.requestEmailVerification.mockResolvedValueOnce(fail(503, "email_delivery_failed"));
    render(<AcceptInvitation token="inv-token-123" userName="Alex" userEmail="a@b.c" />);
    await userEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
    await userEvent.click(await screen.findByRole("button", { name: "Send verification link" }));
    expect(await screen.findByText("Check your inbox for the verification link.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Send again" }));
    expect(await screen.findByText(/couldn't send the email just now/)).toBeVisible();
  });

  it("joins and routes to the workspace once verified", async () => {
    sdk.acceptInvitation.mockResolvedValue(ok({ workspace_id: "w1" }));
    sdk.listWorkspaces.mockResolvedValue(ok({ data: [{ id: "w1", slug: "acme" }] }));
    render(<AcceptInvitation token="inv-token-123" userName="Alex" userEmail="a@b.c" />);
    await userEvent.click(screen.getByRole("button", { name: "Accept invitation" }));
    await vi.waitFor(() => expect(router.replace).toHaveBeenCalledWith("/w/acme/dashboard"));
  });
});

describe("/verify-email/[token]", () => {
  it("verifies only on an explicit click, then links into the app", async () => {
    sdk.verifyEmail.mockResolvedValue({ data: {}, response: new Response(null, { status: 204 }) });
    render(<VerifyEmail token="verify-token-1" />);
    expect(sdk.verifyEmail).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Verify email" }));
    expect(sdk.verifyEmail).toHaveBeenCalledWith(expect.objectContaining({ body: { token: "verify-token-1" } }));
    expect(await screen.findByRole("heading", { name: "Email verified" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Continue to Mera" })).toHaveAttribute("href", "/");
  });

  it("explains an invalid or expired link", async () => {
    sdk.verifyEmail.mockResolvedValue(fail(404, "invalid_verification"));
    render(<VerifyEmail token="verify-token-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Verify email" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
  });
});
