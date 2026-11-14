import type { Metadata } from "next";
import { LoginForm } from "@/components/auth/login-form";
import { safeNextPath } from "@/lib/session/next-path";
import { oauthErrorMessage } from "@/lib/session/oauth";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[]; oauth_error?: string | string[]; mfa?: string | string[] }>;
}) {
  const { next: raw, oauth_error: oauthError, mfa } = await searchParams;
  const next = safeNextPath(typeof raw === "string" ? raw : undefined);
  const registerHref = next === "/" ? "/register" : `/register?next=${encodeURIComponent(next)}`;
  return (
    <LoginForm
      next={next}
      registerHref={registerHref}
      oauthError={typeof oauthError === "string" ? oauthErrorMessage(oauthError) : undefined}
      mfaPending={mfa === "1"}
    />
  );
}
