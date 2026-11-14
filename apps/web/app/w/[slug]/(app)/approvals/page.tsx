import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ApprovalsScreen } from "@/components/approvals/approvals-screen";
import { getApprovalRequests, getSession } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.approvals.title };

/*
 * The Approval Queue (approvals.html). Live since Sprint 8 (M6): open requests
 * with per-account decisions, and the most recently decided. What a member sees
 * is limited to the accounts in their scope; the API decides who may approve.
 */
export default async function ApprovalsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/approvals`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const requests = await getApprovalRequests(workspace.id, { state: "", limit: 40 }, here);
  return (
    <ApprovalsScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone, archived: workspace.archived }}
      requests={requests}
    />
  );
}
