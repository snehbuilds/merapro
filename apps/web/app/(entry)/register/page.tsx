import type { Metadata } from "next";
import { RegisterForm } from "@/components/auth/register-form";
import { safeNextPath } from "@/lib/session/next-path";

export const metadata: Metadata = { title: "Create account" };

export default async function RegisterPage({ searchParams }: { searchParams: Promise<{ next?: string | string[] }> }) {
  const { next: raw } = await searchParams;
  const next = safeNextPath(typeof raw === "string" ? raw : undefined);
  const loginHref = next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`;
  return <RegisterForm next={next} loginHref={loginHref} />;
}
