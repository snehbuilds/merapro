import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DiscoveryScreen } from "@/components/discovery/discovery-screen";
import { getContentStats, getGroups, getPillars, getSession, getTrending, optional } from "@/lib/api/server";
import { MIN_RELEVANCE, TRENDING_LIMIT, WINDOW_HOURS, parseDiscoveryQuery } from "@/lib/discovery";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isEditorRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.discovery.title };

/*
 * Content Discovery (discovery.html). Live since Sprint 5 (M4): trending
 * items ranked per group (or workspace-wide), duplicates collapsed into
 * their original, 7-day counts and the group's pillars. Dismiss and pillar
 * edits need Editor+. ?group=<id>|workspace&window=24h|7d&min=0.5 keep the
 * filters across reloads. "Draft for group" opens the Composer (M5); best
 * sources (M8) come later.
 */
export default async function DiscoveryPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const query = await searchParams;
  const here = `/w/${slug}/discovery`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const groups = await getGroups(workspace.id, here);
  const state = parseDiscoveryQuery(query, groups);
  // The ranked list is the page; the side panels degrade on their own if their call fails.
  const [items, stats, pillars] = await Promise.all([
    getTrending(
      workspace.id,
      {
        groupId: state.groupId,
        hours: WINDOW_HOURS[state.window],
        minRelevance: state.min ? MIN_RELEVANCE : 0,
        limit: TRENDING_LIMIT,
      },
      here,
    ),
    optional(getContentStats(workspace.id, { groupId: state.groupId }, here)),
    optional(getPillars(workspace.id, state.groupId, here)),
  ]);

  return (
    <DiscoveryScreen
      key={`${state.groupId ?? "workspace"}|${state.window}|${state.min}`}
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
        timezone: workspace.timezone,
        archived: workspace.archived,
      }}
      groups={groups}
      initialState={state}
      items={items}
      stats={stats ?? null}
      pillars={pillars ?? null}
      canManage={isEditorRole(workspace.role)}
    />
  );
}
