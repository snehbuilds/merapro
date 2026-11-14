import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { LibraryScreen } from "@/components/library/library-screen";
import { getGroups, getHashtags, getSession, getTemplates } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isEditorRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.library.title };

/* Library (P2-M1): the hashtag registry and post templates. Editors and up change them. */
export default async function LibraryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/library`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const [hashtags, templates, groups] = await Promise.all([
    getHashtags(workspace.id, here),
    getTemplates(workspace.id, here),
    getGroups(workspace.id, here),
  ]);
  return (
    <LibraryScreen
      workspace={{ id: workspace.id, slug: workspace.slug, archived: workspace.archived }}
      hashtags={hashtags}
      templates={templates}
      groups={groups.map((g) => ({ id: g.id, name: g.name }))}
      canEdit={isEditorRole(workspace.role)}
    />
  );
}
