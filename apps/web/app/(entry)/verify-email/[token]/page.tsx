import type { Metadata } from "next";
import { VerifyEmail } from "@/components/auth/verify-email";

export const metadata: Metadata = { title: "Verify email" };

/*
 * /verify-email/<token>: works signed in or out (proxy.ts does not gate it;
 * the token is the credential). Verifying is an explicit POST, never a side
 * effect of opening the link (mail scanners prefetch links).
 */
export default async function VerifyEmailPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <VerifyEmail token={token} />;
}
