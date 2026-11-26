import { listAutomation, listPolicies } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AutomationScreen } from "@/components/automation/automation-screen";
import { getAccounts, getGroups, getMembers, getSession, optional, serverClient, unwrap } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.automation.title };

/* Automation (M6.12): Manual only in Phase 1, and the kill switch at three scopes. */
export default async function AutomationPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/automation`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const client = await serverClient();
  const [states, groups, accounts, policies, members] = await Promise.all([
    unwrap(listAutomation({ client, path: { workspace_id: workspace.id } }), here),
    optional(getGroups(workspace.id, here)),
    optional(getAccounts(workspace.id, here)),
    optional(unwrap(listPolicies({ client, path: { workspace_id: workspace.id } }), here)),
    optional(getMembers(workspace.id, here)),
  ]);
  const canApprove = new Set(["owner", "admin", "editor", "approver"]);
  return (
    <AutomationScreen
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        timezone: workspace.timezone,
        archived: workspace.archived,
        role: workspace.role,
      }}
      states={states.data}
      groups={(groups ?? []).map((g) => ({ id: g.id, name: g.name }))}
      accounts={(accounts ?? []).map((a) => ({ id: a.id, nickname: a.nickname, handle: a.handle, platform: a.platform, avatar_url: a.avatar_url }))}
      policies={policies?.data}
      approvers={(members ?? []).filter((m) => canApprove.has(m.role)).map((m) => ({ id: m.user_id, name: m.display_name }))}
    />
  );
}
