import type { Metadata } from "next";
import { AcceptInvitation } from "@/components/auth/accept-invitation";
import { getSession } from "@/lib/api/server";

export const metadata: Metadata = { title: "Join a workspace" };

/*
 * /invite/<token>: the link an admin shares (no email yet). Signed out →
 * proxy.ts sends the browser to /login?next=/invite/<token> and back here.
 * Accepting is an explicit click (a POST), never a side effect of the GET.
 */
export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const { user } = await getSession(`/invite/${encodeURIComponent(token)}`);
  return <AcceptInvitation token={token} userName={user.display_name} userEmail={user.email} />;
}
