// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const sdk = { connectAccount: vi.fn(), signInAccount: vi.fn() };
vi.mock("@mera/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mera/sdk")>();
  const mocked = Object.fromEntries(Object.keys(sdk).map((k) => [k, (...a: unknown[]) => sdk[k as keyof typeof sdk](...a)]));
  return { ...actual, ...mocked };
});

const { ConnectMenu } = await import("@/components/accounts/connect-menu");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}
function fail(status: number, code: string, message = code) {
  return Promise.resolve({ error: { error: { code, message } }, response: new Response(null, { status }) });
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

async function open() {
  render(<ConnectMenu workspaceId="ws-1" slug="acme" />);
  await userEvent.click(screen.getByRole("button", { name: /Connect account/ }));
}

describe("Connect menu (P2-M3)", () => {
  it("lists every platform as connectable", async () => {
    await open();
    const group = screen.getByRole("group", { name: "Connect an account" });
    expect(within(group).getByRole("button", { name: /Bluesky/ })).toBeEnabled();
    expect(within(group).getByRole("button", { name: /Mastodon/ })).toBeEnabled();
    expect(within(group).queryByText("Phase 2")).toBeNull();
  });

  it("connects Bluesky with a handle and an app password, then hands the code over", async () => {
    sdk.signInAccount.mockReturnValueOnce(fail(422, "sign_in_failed", "Use an app password (xxxx-xxxx-xxxx-xxxx), not your account password."));
    sdk.signInAccount.mockReturnValue(ok({ connect_code: "code-abc-123" }));
    await open();
    await userEvent.click(screen.getByRole("button", { name: /Bluesky/ }));
    const form = screen.getByRole("form", { name: "Connect Bluesky" });
    await userEvent.type(within(form).getByLabelText("Handle"), "acme.bsky.social");
    await userEvent.type(within(form).getByLabelText(/^App password/), "not-an-app-password");
    await userEvent.click(within(form).getByRole("button", { name: "Connect Bluesky" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("app password");
    // The secret is cleared after every attempt.
    expect(within(form).getByLabelText(/^App password/)).toHaveValue("");
    await userEvent.type(within(form).getByLabelText(/^App password/), "abcd-efgh-ijkl-mnop");
    await userEvent.click(within(form).getByRole("button", { name: "Connect Bluesky" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/w/acme/accounts?connect_code=code-abc-123"));
    expect(sdk.signInAccount).toHaveBeenLastCalledWith(
      expect.objectContaining({ body: { identifier: "acme.bsky.social", secret: "abcd-efgh-ijkl-mnop" } }),
    );
  });

  it("asks for the Mastodon server before redirecting", async () => {
    sdk.connectAccount.mockReturnValue(fail(400, "invalid_instance"));
    await open();
    await userEvent.click(screen.getByRole("button", { name: /Mastodon/ }));
    expect(sdk.connectAccount).not.toHaveBeenCalled();
    const form = screen.getByRole("form", { name: "Connect Mastodon" });
    await userEvent.type(within(form).getByLabelText(/^Server/), "intranet");
    await userEvent.click(within(form).getByRole("button", { name: "Connect Mastodon" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("public Mastodon server");
    expect(sdk.connectAccount).toHaveBeenCalledWith(
      expect.objectContaining({ body: { return_path: "/w/acme/accounts", instance: "intranet" } }),
    );
  });
});
