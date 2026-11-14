// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toApiError } from "@/lib/api/errors";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const sessionLogin = vi.fn();
const sessionRegister = vi.fn();
vi.mock("@/lib/api/browser", () => ({
  sessionLogin: (...args: unknown[]) => sessionLogin(...args),
  sessionRegister: (...args: unknown[]) => sessionRegister(...args),
}));

const { LoginForm } = await import("@/components/auth/login-form");
const { RegisterForm } = await import("@/components/auth/register-form");

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("LoginForm", () => {
  it("has no axe violations", async () => {
    const { container } = render(<LoginForm next="/" registerHref="/register" />);
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
  });

  it("goes to `next` after signing in", async () => {
    sessionLogin.mockResolvedValue({ ok: true, data: {} });
    render(<LoginForm next="/w/acme/team" registerHref="/register" />);
    await userEvent.type(screen.getByLabelText("Email"), "a@b.c");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(sessionLogin).toHaveBeenCalledWith({ email: "a@b.c", password: "pw" });
    expect(router.replace).toHaveBeenCalledWith("/w/acme/team");
  });

  it("shows invalid_credentials as a form-level alert", async () => {
    sessionLogin.mockResolvedValue({
      ok: false,
      error: toApiError({ error: { code: "invalid_credentials", message: "Email or password is wrong." } }, 401),
    });
    render(<LoginForm next="/" registerHref="/register" />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is wrong.");
  });

  it("says how long to wait when rate limited", async () => {
    sessionLogin.mockResolvedValue({
      ok: false,
      error: toApiError({ error: { code: "rate_limited", message: "Too many requests." } }, 429, new Headers({ "retry-after": "42" })),
    });
    render(<LoginForm next="/" registerHref="/register" />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts, try again in 42 seconds.");
  });

  it("shows an OAuth failure passed from the callback", () => {
    render(<LoginForm next="/" registerHref="/register" oauthError="An account with this email exists — sign in with your password." />);
    expect(screen.getByRole("alert")).toHaveTextContent("An account with this email exists");
    expect(screen.getByRole("link", { name: "Continue with Google" })).toHaveAttribute("href", "/api/session/oauth/google/start");
  });
});

describe("RegisterForm", () => {
  it("puts the API's field errors under their fields and wires aria-invalid", async () => {
    sessionRegister.mockResolvedValue({
      ok: false,
      error: toApiError(
        {
          error: {
            code: "password_breached",
            message: "Choose another.",
            field_errors: [{ field: "password", code: "password_breached", message: "This password appears in known breaches." }],
          },
        },
        422,
      ),
    });
    render(<RegisterForm next="/" loginHref="/login" />);
    await userEvent.type(screen.getByLabelText("Your name"), "Alex");
    await userEvent.type(screen.getByLabelText("Email"), "a@b.c");
    await userEvent.type(screen.getByLabelText("Password"), "password12345");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    const password = screen.getByLabelText("Password");
    expect(await screen.findByText("This password appears in known breaches.")).toBeVisible();
    expect(password).toHaveAttribute("aria-invalid", "true");
    expect(password.getAttribute("aria-describedby")).toMatch(/-error/);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
