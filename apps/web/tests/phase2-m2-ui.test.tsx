// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const sdk = { secondFactor: vi.fn(), mfaSetup: vi.fn(), mfaConfirm: vi.fn(), setPin: vi.fn() };
vi.mock("@mera/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mera/sdk")>();
  const mocked = Object.fromEntries(Object.keys(sdk).map((k) => [k, (...a: unknown[]) => sdk[k as keyof typeof sdk](...a)]));
  return { ...actual, ...mocked };
});

const browser = vi.hoisted(() => ({ sessionLogin: vi.fn(), sessionMfa: vi.fn() }));
vi.mock("@/lib/api/browser", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/browser")>();
  return { ...actual, sessionLogin: browser.sessionLogin, sessionMfa: browser.sessionMfa };
});

const { LoginForm } = await import("@/components/auth/login-form");
const { SecondFactorProvider, useSecondFactor } = await import("@/components/mera/second-factor");
const { SecurityScreen } = await import("@/components/security/security-screen");
const { ShellProvider } = await import("@/components/shell/shell-provider");

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

const shellWs = { id: "ws-1", slug: "acme", timezone: "UTC", archived: false, name: "Acme", mark: "AC", meta: "Owner", role: "owner" };
const shellUser = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };
function inShell(node: React.ReactNode) {
  return (
    <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={shellUser}>
      {node}
    </ShellProvider>
  );
}

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("login with two-step sign-in", () => {
  it("asks for the code after the password, then signs in", async () => {
    browser.sessionLogin.mockResolvedValue({ ok: false, error: { code: "mfa_required", message: "Enter the code", status: 401, fieldErrors: {}, fieldCodes: {} } });
    browser.sessionMfa
      .mockResolvedValueOnce({ ok: false, error: { code: "invalid_mfa_code", message: "That code is not right.", status: 401, fieldErrors: {}, fieldCodes: {} } })
      .mockResolvedValue({ ok: true, data: null });
    render(<LoginForm next="/w/acme/dashboard" registerHref="/register" />);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.c");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Two-step sign-in" })).toBeVisible();
    await userEvent.type(screen.getByLabelText("Code"), "000000");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not right");
    await userEvent.type(screen.getByLabelText("Code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/w/acme/dashboard"));
    expect(browser.sessionMfa).toHaveBeenLastCalledWith({ code: "123456" });
  });

  it("starts on the code step after an OAuth sign-in that needs one", () => {
    render(<LoginForm next="/" registerHref="/register" mfaPending />);
    expect(screen.getByRole("heading", { name: "Two-step sign-in" })).toBeVisible();
  });
});

function Guarded({ onResult }: { onResult: (v: unknown) => void }) {
  const { withSecondFactor } = useSecondFactor();
  return (
    <button
      type="button"
      onClick={async () => {
        const calls: Record<string, string>[] = [];
        const r = await withSecondFactor(async (headers) => {
          calls.push(headers);
          if (!headers["x-mera-second-factor"]) {
            return { ok: false as const, error: { code: "second_factor_required", message: "x", status: 403, fieldErrors: { kill_switch_resume: "x" }, fieldCodes: {} } };
          }
          return { ok: true as const, data: "done" };
        });
        onResult({ r, calls });
      }}
    >
      Resume
    </button>
  );
}

describe("second factor prompt", () => {
  it("asks once, gets a grant and retries with it", async () => {
    sdk.secondFactor.mockReturnValue(ok({ token: "mera_sf_tok", expires_at: "2026-11-10T00:05:00Z" }));
    const onResult = vi.fn();
    render(
      <SecondFactorProvider workspaceId="ws-1" slug="acme">
        <Guarded onResult={onResult} />
      </SecondFactorProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Resume automation needs a second factor/)).toBeVisible();
    await userEvent.click(within(dialog).getByRole("radio", { name: "PIN" }));
    await userEvent.type(within(dialog).getByLabelText("Approval PIN"), "482916");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(onResult).toHaveBeenCalled());
    expect(sdk.secondFactor).toHaveBeenCalledWith(
      expect.objectContaining({ body: { action: "kill_switch_resume", method: "pin", value: "482916" } }),
    );
    const { r, calls } = onResult.mock.calls[0]?.[0] as { r: { ok: boolean }; calls: Record<string, string>[] };
    expect(r.ok).toBe(true);
    expect(calls).toEqual([{}, { "x-mera-second-factor": "mera_sf_tok" }]);
  });

  it("returns the refusal when cancelled", async () => {
    const onResult = vi.fn();
    render(
      <SecondFactorProvider workspaceId="ws-1" slug="acme">
        <Guarded onResult={onResult} />
      </SecondFactorProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onResult).toHaveBeenCalled());
    expect((onResult.mock.calls[0]?.[0] as { r: { ok: boolean } }).r.ok).toBe(false);
    expect(sdk.secondFactor).not.toHaveBeenCalled();
  });
});

describe("Your security", () => {
  const status = {
    mfa_enabled: false,
    mfa_enabled_at: null,
    mfa_pending: false,
    recovery_codes_left: 0,
    pin_set: false,
    pin_locked_until: null,
    mfa_required: true,
  };

  it("sets up two-step sign-in and shows the recovery codes once", async () => {
    sdk.mfaSetup.mockReturnValue(ok({ secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/Mera:a@b.c?secret=JBSWY3DPEHPK3PXP" }));
    sdk.mfaConfirm.mockReturnValue(ok({ recovery_codes: ["aaaaa-11111", "bbbbb-22222"] }));
    render(inShell(<SecurityScreen workspace={{ id: "ws-1", slug: "acme", timezone: "UTC" }} status={status} me={{ id: "u1", awayUntil: null, delegateId: null }} delegates={[]} />));
    expect(screen.getByText(/limited to an Editor/)).toBeVisible();
    // The password comes first: a session alone cannot enrol an authenticator.
    const start = screen.getByRole("button", { name: "Set up two-step sign-in" });
    expect(start).toBeDisabled();
    await userEvent.type(within(screen.getByRole("form", { name: "Start two-step sign-in" })).getByLabelText("Your password"), "pw-123");
    await userEvent.click(start);
    expect(sdk.mfaSetup).toHaveBeenCalledWith(expect.objectContaining({ body: { proof: "pw-123" } }));
    expect(await screen.findByLabelText("Setup key")).toHaveTextContent("JBSWY3DPEHPK3PXP");
    await userEvent.type(screen.getByLabelText("Code from the app"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Turn on" }));
    expect(await screen.findByLabelText("Recovery codes")).toHaveTextContent("aaaaa-11111");
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: "I've saved them" }));
    });
    expect(screen.queryByLabelText("Recovery codes")).toBeNull();
  });

  it("refuses a weak PIN with the API's reason", async () => {
    sdk.setPin.mockReturnValue(Promise.resolve({ error: { error: { code: "invalid_pin", message: "x" } }, response: new Response(null, { status: 422 }) }));
    render(inShell(<SecurityScreen workspace={{ id: "ws-1", slug: "acme", timezone: "UTC" }} status={status} me={{ id: "u1", awayUntil: null, delegateId: null }} delegates={[]} />));
    const form = screen.getByRole("form", { name: "Approval PIN" });
    await userEvent.type(within(form).getByLabelText(/^PIN/), "111111");
    await userEvent.type(within(form).getByLabelText("Your password"), "pw");
    await userEvent.click(within(form).getByRole("button", { name: "Set PIN" }));
    expect(await screen.findByText("Use 6 to 8 digits, not all the same.")).toBeVisible();
  });
});
