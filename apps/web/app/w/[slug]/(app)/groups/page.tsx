import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { GroupsScreen } from "@/components/groups/groups-screen";
import { getAccounts, getGroups, getSession } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isEditorRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.groups.title };

/*
 * Account Groups (groups.html). Live since Sprint 4 (M3): groups, ordered
 * membership, stagger, resolved settings. Voice, pillars and linked sources
 * (the mockup's "Content" panel) arrive with M4/M5.
 */
export default async function GroupsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/groups`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const [groups, accounts] = await Promise.all([getGroups(workspace.id, here), getAccounts(workspace.id, here)]);

  return (
    <GroupsScreen
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
        timezone: workspace.timezone,
        archived: workspace.archived,
      }}
      groups={groups}
      accounts={accounts}
      canManage={isEditorRole(workspace.role)}
    />
  );
}
