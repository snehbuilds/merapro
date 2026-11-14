import { securityStatus } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SecurityScreen } from "@/components/security/security-screen";
import { getMembers, getSession, serverClient, unwrap } from "@/lib/api/server";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: "Your security" };

const CAN_APPROVE = new Set(["owner", "admin", "editor", "approver"]);

/* Your security (P2-M2): two-step sign-in, the approval PIN, out of office. */
export default async function SecurityPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/security`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const [status, members] = await Promise.all([
    unwrap(securityStatus({ client: await serverClient() }), here),
    getMembers(workspace.id, here),
  ]);
  const mine = members.find((m) => m.user_id === session.user.id);
  return (
    <SecurityScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone }}
      status={status}
      me={{ id: session.user.id, awayUntil: mine?.away_until ?? null, delegateId: mine?.delegate_user_id ?? null }}
      delegates={members
        .filter((m) => m.user_id !== session.user.id && CAN_APPROVE.has(m.role))
        .map((m) => ({ id: m.user_id, name: m.display_name }))}
    />
  );
}
