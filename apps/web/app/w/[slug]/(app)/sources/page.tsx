import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SourcesScreen } from "@/components/sources/sources-screen";
import { getGroups, getSession, getSources } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isEditorRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.sources.title };

/*
 * Sources (sources.html). Live since Sprint 5 (M4): RSS / Atom feeds, their
 * group links and health; add, test, edit, pause, resume, fetch now, delete
 * (Editor+, CONFIGURE_CONTENT). Other source types are listed but not yet
 * available. ?source=<id> preselects a source.
 */
export default async function SourcesPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const query = await searchParams;
  const here = `/w/${slug}/sources`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const [sources, groups] = await Promise.all([getSources(workspace.id, here), getGroups(workspace.id, here)]);

  return (
    <SourcesScreen
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
        timezone: workspace.timezone,
        archived: workspace.archived,
      }}
      sources={sources}
      groups={groups}
      canManage={isEditorRole(workspace.role)}
      initialSourceId={typeof query.source === "string" ? query.source : undefined}
    />
  );
}
