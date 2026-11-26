import {
  calendar,
  listDeadLetters,
  listInsights,
  overview,
  timeseries,
  topPosts,
  trending,
} from "@mera/sdk";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  DashboardBento,
  type DashboardActivity,
  type DashboardSuggestion,
  type DashboardTopPost,
} from "@/components/dashboard/dashboard-bento";
import { AttentionNotices } from "@/components/shell/attention-notices";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { dashboardAttention } from "@/lib/accounts";
import { windowFrom, type Point } from "@/lib/analytics";
import {
  getAccounts,
  getApprovalRequests,
  getNotifications,
  getSession,
  optional,
  serverClient,
  unwrap,
} from "@/lib/api/server";
import { aroundNow, localDate } from "@/lib/calendar";
import { SCREENS } from "@/lib/nav";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.dashboard.title };

function buildWeekDays(
  tz: string,
  items: readonly { state: string; scheduled_for: string; published_at: string | null }[],
) {
  const now = new Date();
  const todayStr = localDate(now, tz);
  const dowShort = new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short" });
  const dayNumFmt = new Intl.DateTimeFormat("en-US", { timeZone: tz, day: "numeric" });
  const monthLabel = new Intl.DateTimeFormat("en-US", { timeZone: tz, month: "short", year: "numeric" }).format(now);

  // Start the 7-day strip on Monday of the current week (in workspace tz).
  const currentDow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(dowShort.format(now));
  const offsetToMon = currentDow >= 0 ? -currentDow : 0;

  const byDate = new Map<string, ("published" | "queued" | "failed")[]>();
  for (const s of items) {
    const key = localDate(s.published_at ?? s.scheduled_for, tz);
    const kind = s.state === "published" ? "published" : s.state === "failed" || s.state === "dead_lettered" ? "failed" : "queued";
    const list = byDate.get(key) ?? [];
    list.push(kind);
    byDate.set(key, list);
  }

  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(now.getTime() + (offsetToMon + i) * 86_400_000);
    const date = localDate(d, tz);
    return {
      date,
      dow: dowShort.format(d),
      dayNum: dayNumFmt.format(d),
      isToday: date === todayStr,
      dots: byDate.get(date) ?? [],
    };
  });

  return { weekDays, monthLabel, todayStr, tomorrowStr: localDate(new Date(now.getTime() + 86_400_000), tz) };
}

/*
 * Dashboard (bento grid): greeting, 5 KPI tiles, publishing queue, 7-day calendar strip,
 * connected platforms, 7-day performance chart, top posts, AI suggestions & recent activity.
 */
export default async function DashboardPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/dashboard`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const client = await serverClient();
  const path = { workspace_id: workspace.id };
  const from7d = windowFrom(7);
  const window = aroundNow(7, 7);

  const [accounts, notifications, open, slots, dead, ov, tsReach, tsEng, top, insights, trend] = await Promise.all([
    optional(getAccounts(workspace.id, here)),
    optional(getNotifications(workspace.id, { unreadOnly: true }, here)),
    optional(getApprovalRequests(workspace.id, { state: "open", limit: 20 }, here)),
    optional(unwrap(calendar({ client, path, query: window }), here)),
    optional(unwrap(listDeadLetters({ client, path }), here)),
    optional(unwrap(overview({ client, path, query: { from: from7d } }), here)),
    optional(unwrap(timeseries({ client, path, query: { from: from7d, metric: "impressions" } }), here)),
    optional(unwrap(timeseries({ client, path, query: { from: from7d, metric: "engagement" } }), here)),
    optional(unwrap(topPosts({ client, path, query: { from: from7d, limit: 3 } }), here)),
    optional(unwrap(listInsights({ client, path, query: { limit: 3 } }), here)),
    optional(unwrap(trending({ client, path, query: { limit: 3 } }), here)),
  ]);

  const attention = dashboardAttention(accounts ?? [], notifications ?? []);
  const screen = SCREENS.dashboard;
  const items = slots?.data ?? [];
  const tz = workspace.timezone;

  const { weekDays, monthLabel, todayStr, tomorrowStr } = buildWeekDays(tz, items);
  const upcoming = items.filter((s) => s.state === "queued" || s.state === "failed" || s.state === "publishing");
  const todayCount = upcoming.filter((s) => localDate(s.scheduled_for, tz) === todayStr).length;
  const tomorrowCount = upcoming.filter((s) => localDate(s.scheduled_for, tz) === tomorrowStr).length;
  const waitingApprovals = (open ?? []).reduce((n, r) => n + r.variants.filter((v) => v.can_decide).length, 0);
  const failing = (dead?.data ?? []).length;

  const totals = ((ov as { totals?: Record<string, number | null> } | null)?.totals ?? {}) as Record<string, number | null>;
  const published7d = totals.posts ?? items.filter((s) => s.state === "published").length;
  const engagements7d = totals.engagement ?? null;
  const reach7d = totals.reach ?? totals.impressions ?? null;

  const reachSeries = ((tsReach as { points?: Point[] } | null)?.points ?? []) as Point[];
  const engagementSeries = ((tsEng as { points?: Point[] } | null)?.points ?? []) as Point[];
  const topList = ((top as { data?: DashboardTopPost[] } | null)?.data ?? []) as DashboardTopPost[];

  const insightItems = ((insights as { data?: Record<string, unknown>[] } | null)?.data ?? []).map((ins) => ({
    id: String(ins.id ?? Math.random()),
    title: String(ins.title ?? ins.summary ?? "Performance insight"),
    subtitle: String(ins.body ?? ins.recommendation ?? "View details in Analytics"),
    href: `/w/${slug}/analytics`,
  }));
  const trendingItems = ((trend as { data?: Record<string, unknown>[] } | null)?.data ?? []).map((it) => ({
    id: String(it.id ?? Math.random()),
    title: String(it.title ?? "Trending article"),
    subtitle: String(it.source_name ?? it.url ?? "Draft a post from Discovery"),
    href: `/w/${slug}/composer?item=${encodeURIComponent(String(it.id ?? ""))}`,
  }));
  const suggestions: DashboardSuggestion[] = [...insightItems, ...trendingItems].slice(0, 3);

  const activities: DashboardActivity[] = [
    ...items
      .filter((s) => s.state === "published" || s.state === "queued" || s.state === "failed")
      .slice(-6)
      .reverse()
      .map((s) => ({
        id: s.id,
        kind: (s.state === "published" ? "published" : s.state === "failed" ? "failed" : "scheduled") as
          | "published"
          | "failed"
          | "scheduled",
        label:
          s.state === "published"
            ? `Published to ${s.account_nickname}`
            : s.state === "failed"
              ? `Failed on ${s.account_nickname}`
              : `Scheduled for ${s.account_nickname}`,
        detail: s.post_set_title || s.excerpt,
        at: s.published_at ?? s.scheduled_for,
      })),
  ];

  const firstName = (session.user.display_name || session.user.email || "there").split(/\s+/)[0] ?? "there";

  return (
    <>
      <Topbar title={screen.title} action={screen.action} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <AttentionNotices items={attention} slug={slug} />
        {failing ? (
          <Notice
            tone="danger"
            title={`${failing} post${failing === 1 ? "" : "s"} failed to publish`}
            actions={
              <Button asChild size="sm">
                <Link href={`/w/${slug}/calendar`}>Review on the calendar</Link>
              </Button>
            }
          >
            The other accounts were not affected.
          </Notice>
        ) : null}

        <DashboardBento
          slug={slug}
          firstName={firstName}
          timezone={tz}
          waitingApprovals={waitingApprovals}
          scheduledCount={upcoming.length}
          published7d={published7d}
          engagements7d={engagements7d}
          reach7d={reach7d}
          todayCount={todayCount}
          tomorrowCount={tomorrowCount}
          weekCount={upcoming.length}
          weekDays={weekDays}
          monthLabel={monthLabel}
          accounts={(accounts ?? []).map((a) => ({
            id: a.id,
            platform: a.platform,
            nickname: a.nickname,
            status: a.status,
          }))}
          reachSeries={reachSeries}
          engagementSeries={engagementSeries}
          topPosts={topList}
          suggestions={suggestions}
          activities={activities}
        />
      </main>
    </>
  );
}
