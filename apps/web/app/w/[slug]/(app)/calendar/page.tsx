import { calendar, listDeadLetters } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CalendarScreen } from "@/components/calendar/calendar-screen";
import { getGroups, getSession, optional, serverClient, unwrap } from "@/lib/api/server";
import { addDays, localDate, weekStart, zonedToUtc } from "@/lib/calendar";
import { SCREENS } from "@/lib/nav";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.calendar.title };

/*
 * Content Calendar (M8.12): one week, in the workspace time zone, optionally one group.
 * ?week=YYYY-MM-DD (any day of the week) and ?group=<id>.
 */
export default async function CalendarPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ week?: string; group?: string }>;
}) {
  const { slug } = await params;
  const q = await searchParams;
  const here = `/w/${slug}/calendar`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const tz = workspace.timezone;
  const day = q.week && /^\d{4}-\d{2}-\d{2}$/.test(q.week) ? q.week : localDate(new Date(), tz);
  const week = weekStart(day);
  const groupId = q.group && /^[0-9a-f-]{36}$/.test(q.group) ? q.group : null;
  const client = await serverClient();
  const [items, dead, groups] = await Promise.all([
    unwrap(
      calendar({
        client,
        path: { workspace_id: workspace.id },
        query: {
          from: zonedToUtc(week, "00:00", tz).toISOString(),
          to: zonedToUtc(addDays(week, 7), "00:00", tz).toISOString(),
          group_id: groupId,
        },
      }),
      here,
    ),
    optional(unwrap(listDeadLetters({ client, path: { workspace_id: workspace.id } }), here)),
    optional(getGroups(workspace.id, here)),
  ]);
  return (
    <CalendarScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: tz, archived: workspace.archived }}
      week={week}
      items={items.data}
      deadLetters={dead?.data ?? []}
      groups={(groups ?? []).map((g) => ({ id: g.id, name: g.name }))}
      groupId={groupId}
    />
  );
}
