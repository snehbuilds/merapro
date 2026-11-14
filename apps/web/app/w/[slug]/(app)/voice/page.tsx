import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { VoiceScreen } from "@/components/voice/voice-screen";
import { getAccounts, getGroups, getSession, getVoices } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isEditorRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.voice.title };

/*
 * Brand Voice (brand-voice.html). Live since Sprint 6 (M5): profiles at
 * workspace, group and account scope, one default per scope, a sample
 * drafted with a profile. Editors and up change voices (CONFIGURE_CONTENT);
 * "Learned from your edits" fills in with approvals (M6).
 */
export default async function VoicePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/voice`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const [voices, groups, accounts] = await Promise.all([
    getVoices(workspace.id, here),
    getGroups(workspace.id, here),
    getAccounts(workspace.id, here),
  ]);

  return (
    <VoiceScreen
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
        timezone: workspace.timezone,
        archived: workspace.archived,
      }}
      voices={voices}
      groups={groups}
      accounts={accounts}
      canManage={isEditorRole(workspace.role)}
    />
  );
}
