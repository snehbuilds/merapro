import { accountComparison, approvalTurnaround, groupComparison, overview, timeseries, topPosts } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AnalyticsScreen } from "@/components/analytics/analytics-screen";
import { getAccounts, getGroups, getSession, optional, serverClient, unwrap } from "@/lib/api/server";
import { windowFrom, type Point } from "@/lib/analytics";
import { SCREENS } from "@/lib/nav";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.analytics.title };

const UUID = /^[0-9a-f-]{36}$/;

/* Analytics (M10). ?days=7|30|90, ?group=<id> or ?account=<id>. */
export default async function AnalyticsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ days?: string; group?: string; account?: string }>;
}) {
  const { slug } = await params;
  const q = await searchParams;
  const here = `/w/${slug}/analytics`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const days = [7, 30, 90].includes(Number(q.days)) ? Number(q.days) : 30;
  const groupId = q.group && UUID.test(q.group) ? q.group : null;
  const accountId = q.account && UUID.test(q.account) ? q.account : null;
  const from = windowFrom(days);
  const client = await serverClient();
  const path = { workspace_id: workspace.id };
  const scope = { from, group_id: groupId, account_id: accountId };
  const [ov, ts, acc, grp, turn, top, groups, accounts] = await Promise.all([
    unwrap(overview({ client, path, query: scope }), here),
    unwrap(timeseries({ client, path, query: { ...scope, metric: "engagement" } }), here),
    unwrap(accountComparison({ client, path, query: { from, group_id: groupId } }), here),
    unwrap(groupComparison({ client, path, query: { from } }), here),
    unwrap(approvalTurnaround({ client, path, query: { from, group_id: groupId } }), here),
    unwrap(topPosts({ client, path, query: { ...scope, limit: 8 } }), here),
    optional(getGroups(workspace.id, here)),
    optional(getAccounts(workspace.id, here)),
  ]);
  return (
    <AnalyticsScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone, role: workspace.role }}
      filters={{ days, groupId, accountId, from }}
      groups={(groups ?? []).map((g) => ({ id: g.id, name: g.name }))}
      accounts={(accounts ?? []).map((a) => ({ id: a.id, nickname: a.nickname }))}
      overview={ov as Record<string, unknown>}
      series={ts as unknown as { metric: string; points: Point[] }}
      accountsCmp={(acc as { data: Record<string, unknown>[] }).data}
      groupsCmp={(grp as { data: Record<string, unknown>[] }).data}
      turnaround={turn as Record<string, unknown>}
      top={(top as { data: Record<string, unknown>[] }).data}
    />
  );
}
