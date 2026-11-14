"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot } from "@/components/ui/panel";
import { sessionRegister } from "@/lib/api/browser";
import { formErrors } from "@/lib/api/errors";

const FIELDS = ["display_name", "email", "password"] as const;
type RegisterField = (typeof FIELDS)[number];

/*
 * The API decides password policy (422 password_too_short / password_breached
 * / password_matches_email, 409/422 email_taken); its messages show inline
 * under the field they name.
 */
export function RegisterForm({ next, loginHref }: { next: string; loginHref: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<RegisterField, string>>>({});
  const [formError, setFormError] = useState<string[]>([]);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    setPending(true);
    setFieldErrors({});
    setFormError([]);
    const result = await sessionRegister({
      display_name: String(data.get("display_name") ?? "").trim(),
      email: String(data.get("email") ?? "").trim(),
      password: String(data.get("password") ?? ""),
    });
    if (result.ok) {
      router.replace(next);
      router.refresh();
      return;
    }
    setPending(false);
    const split = formErrors(result.error, FIELDS);
    setFieldErrors(split.fields);
    setFormError(split.form);
  }

  return (
    <Panel aria-labelledby="register-title">
      <PanelBody className="grid gap-5 p-6 max-narrow:p-4">
        <div className="grid gap-1">
          <h1 id="register-title" className="text-lg">
            Create your account
          </h1>
          <p className="text-sm text-fg-muted">You can create or join a workspace next.</p>
        </div>
        <form className="grid gap-4" onSubmit={onSubmit} noValidate>
          {formError.length > 0 ? (
            <Notice tone="danger" role="alert">
              {formError.join(" ")}
            </Notice>
          ) : null}
          <Field label="Your name" error={fieldErrors.display_name}>
            <Input name="display_name" autoComplete="name" required maxLength={120} autoFocus />
          </Field>
          <Field label="Email" error={fieldErrors.email}>
            <Input name="email" type="email" autoComplete="email" required />
          </Field>
          <Field
            label="Password"
            hint="At least 12 characters. Not your email, and not a password seen in a known breach."
            error={fieldErrors.password}
          >
            <Input name="password" type="password" autoComplete="new-password" required minLength={12} />
          </Field>
          <Button type="submit" variant="primary" disabled={pending} className="w-full">
            {pending ? "Creating account…" : "Create account"}
          </Button>
        </form>
      </PanelBody>
      <PanelFoot>
        Already have an account? <Link href={loginHref}>Sign in</Link>
      </PanelFoot>
    </Panel>
  );
}
