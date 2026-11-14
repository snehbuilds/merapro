"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot } from "@/components/ui/panel";
import { sessionLogin, sessionMfa } from "@/lib/api/browser";
import { formErrors } from "@/lib/api/errors";

const FIELDS = ["email", "password"] as const;
type LoginField = (typeof FIELDS)[number];

/** `next` is already validated server-side (safeNextPath). */
export function LoginForm({
  next,
  registerHref,
  oauthError,
  mfaPending = false,
}: {
  next: string;
  registerHref: string;
  /** Message for a failed Google / Microsoft sign-in (?oauth_error=). */
  oauthError?: string;
  /** ?mfa=1: an OAuth sign-in is waiting for its second step. */
  mfaPending?: boolean;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [step, setStep] = useState<"password" | "code">(mfaPending ? "code" : "password");
  const [code, setCode] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<LoginField, string>>>({});
  const [formError, setFormError] = useState<string[]>(oauthError ? [oauthError] : []);
  const oauthQuery = next === "/" ? "" : `?next=${encodeURIComponent(next)}`;

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setPending(true);
    setFieldErrors({});
    setFormError([]);
    const result = await sessionLogin({
      email: String(data.get("email") ?? ""),
      password: String(data.get("password") ?? ""),
    });
    if (result.ok) {
      router.replace(next);
      router.refresh();
      return;
    }
    setPending(false);
    if (result.error.code === "mfa_required") {
      setStep("code");
      return;
    }
    if (result.error.code === "invalid_credentials") {
      setFormError([result.error.message || "That email and password don't match."]);
      return;
    }
    const split = formErrors(result.error, FIELDS);
    setFieldErrors(split.fields);
    setFormError(split.form);
  }

  // P2-M2.3: the second step. The challenge is in an httpOnly cookie; only the code is sent.
  async function onCode(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setFormError([]);
    const result = await sessionMfa({ code: code.trim() });
    if (result.ok) {
      router.replace(next);
      router.refresh();
      return;
    }
    setPending(false);
    setCode("");
    if (result.error.code === "invalid_mfa_challenge") {
      setStep("password");
      setFormError(["That sign-in expired. Sign in again."]);
      return;
    }
    setFormError([result.error.message || "That code is not right."]);
  }

  if (step === "code") {
    return (
      <Panel aria-labelledby="login-title">
        <PanelBody className="grid gap-5 p-6 max-narrow:p-4">
          <div className="grid gap-1">
            <h1 id="login-title" className="text-lg">
              Two-step sign-in
            </h1>
            <p className="text-sm text-fg-muted">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
          </div>
          <form className="grid gap-4" onSubmit={onCode} noValidate aria-label="Authenticator code">
            {formError.length > 0 ? (
              <Notice tone="danger" role="alert">
                {formError.join(" ")}
              </Notice>
            ) : null}
            <Field label="Code">
              <Input
                name="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </Field>
            <Button type="submit" variant="primary" disabled={pending || !code.trim()} className="w-full">
              {pending ? "Checking…" : "Continue"}
            </Button>
          </form>
        </PanelBody>
        <PanelFoot>
          Lost your phone? Use a recovery code. <button type="button" className="underline" onClick={() => setStep("password")}>Start over</button>
        </PanelFoot>
      </Panel>
    );
  }

  return (
    <Panel aria-labelledby="login-title">
      <PanelBody className="grid gap-5 p-6 max-narrow:p-4">
        <div className="grid gap-1">
          <h1 id="login-title" className="text-lg">
            Sign in
          </h1>
          <p className="text-sm text-fg-muted">Approve, schedule and review posts across your accounts.</p>
        </div>
        <form className="grid gap-4" onSubmit={onSubmit} noValidate>
          {formError.length > 0 ? (
            <Notice tone="danger" role="alert">
              {formError.join(" ")}
            </Notice>
          ) : null}
          <Field label="Email" error={fieldErrors.email}>
            <Input name="email" type="email" autoComplete="username" required autoFocus />
          </Field>
          <Field label="Password" error={fieldErrors.password}>
            <Input name="password" type="password" autoComplete="current-password" required />
          </Field>
          <Button type="submit" variant="primary" disabled={pending} className="w-full">
            {pending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <div
          className="flex items-center gap-2.5 font-label text-2xs tracking-[0.12em] text-fg-subtle uppercase before:h-px before:flex-1 before:bg-border before:content-[''] after:h-px after:flex-1 after:bg-border after:content-['']"
        >
          or
        </div>
        <div className="grid gap-2">
          {/* Plain navigations: the start route redirects to the provider. */}
          <Button asChild className="w-full">
            <a href={`/api/session/oauth/google/start${oauthQuery}`}>Continue with Google</a>
          </Button>
          <Button asChild className="w-full">
            <a href={`/api/session/oauth/microsoft/start${oauthQuery}`}>Continue with Microsoft</a>
          </Button>
        </div>
      </PanelBody>
      <PanelFoot>
        New to Mera? <Link href={registerHref}>Create an account</Link>
      </PanelFoot>
    </Panel>
  );
}
