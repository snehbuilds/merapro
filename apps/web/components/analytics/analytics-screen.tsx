"use client";

import { timeseries } from "@mera/sdk";
import Link from "next/link";
import { useState } from "react";
import { InsightsPanels } from "@/components/analytics/insights-panel";
import { BarChart } from "@/components/mera/bar-chart";
import { GroupDot } from "@/components/mera/group-dot";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { useWorkspaceEvents } from "@/hooks/use-workspace-events";
import { platformLabel } from "@/lib/accounts";
import { exportHref, formatIndex, formatMetric, formatMinutes, indexTone, METRIC_LABEL, NOT_REPORTED, type Point } from "@/lib/analytics";
import { browserApi, settle } from "@/lib/api/browser";
import { SCREENS } from "@/lib/nav";

/*
 * Analytics (analytics.html, M10). Scope by group or account and a window; headline
 * tiles where an unreported metric says "Not reported" (never 0); a daily chart; each
 * account against its own baseline, not raw totals; group against group; approval
 * turnaround (Mera's own data, every platform); top posts; CSV export of the same scope.
 * Insights, weights, calibration and side-by-side comparison follow (P3-M1).
 */

type Json = Record<string, unknown>;

export interface AnalyticsScreenProps {
  workspace: { id: string; slug: string; timezone: string; role?: string };
  filters: { days: number; groupId: string | null; accountId: string | null; from: string };
  groups: { id: string; name: string }[];
  accounts: { id: string; nickname: string }[];
  overview: Json;
  series: { metric: string; points: Point[] };
  accountsCmp: Json[];
  groupsCmp: Json[];
  turnaround: Json;
  top: Json[];
}

const TILES = ["posts", "impressions", "reach", "likes", "comments", "shares", "engagement_rate"] as const;
const CHART_METRICS = ["engagement", "impressions", "likes", "comments", "shares", "posts"] as const;

export function AnalyticsScreen(p: AnalyticsScreenProps) {
  const { workspace, filters } = p;
  const [series, setSeries] = useState(p.series);
  const [loading, setLoading] = useState(false);
  useWorkspaceEvents(workspace.id, ["metrics.synced", "post.published"], { debounceMs: 2000 });
  const totals = (p.overview.totals ?? {}) as Record<string, number | null>;
  const qs = (patch: Partial<{ days: number; group: string | null; account: string | null }>) => {
    const next = { days: filters.days, group: filters.groupId, account: filters.accountId, ...patch };
    const q = new URLSearchParams({ days: String(next.days) });
    if (next.group) q.set("group", next.group);
    if (next.account) q.set("account", next.account);
    return `/w/${workspace.slug}/analytics?${q.toString()}`;
  };

  async function pick(metric: string) {
    setLoading(true);
    const r = await settle(
      timeseries({
        client: browserApi,
        path: { workspace_id: workspace.id },
        query: { metric, from: filters.from, group_id: filters.groupId, account_id: filters.accountId },
      }),
    );
    setLoading(false);
    if (r.ok) setSeries(r.data as unknown as { metric: string; points: Point[] });
  }

  const turn = p.turnaround as { count: number; median_minutes: number | null; p90_minutes: number | null; undecided: number; by_group: Json[] };

  return (
    <>
      <Topbar title={SCREENS.analytics.title} sub={`Last ${filters.days} days · ${workspace.timezone}`} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div className="flex flex-wrap items-center gap-2">
          <nav aria-label="Window" className="flex gap-1 text-caption">
            {[7, 30, 90].map((d) => (
              <Link key={d} href={qs({ days: d })} aria-current={filters.days === d ? "page" : undefined} className="rounded-sm px-2 py-1 aria-[current=page]:bg-active">
                {d} days
              </Link>
            ))}
          </nav>
          <span className="text-fg-subtle">·</span>
          <nav aria-label="Scope" className="flex flex-wrap gap-1 text-caption">
            <Link href={qs({ group: null, account: null })} aria-current={!filters.groupId && !filters.accountId ? "page" : undefined} className="rounded-sm px-2 py-1 aria-[current=page]:bg-active">
              Workspace
            </Link>
            {p.groups.map((g) => (
              <Link key={g.id} href={qs({ group: g.id, account: null })} aria-current={filters.groupId === g.id ? "page" : undefined} className="flex items-center gap-1 rounded-sm px-2 py-1 aria-[current=page]:bg-active">
                <GroupDot group={g.id} /> {g.name}
              </Link>
            ))}
          </nav>
          <label className="flex items-center gap-1.5 text-caption">
            <span className="text-fg-subtle">Account</span>
            <select
              className="h-7 rounded-sm border border-border-strong bg-surface px-1.5"
              value={filters.accountId ?? ""}
              onChange={(e) => window.location.assign(qs({ account: e.target.value || null, group: null }))}
            >
              <option value="">All</option>
              {p.accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.nickname}
                </option>
              ))}
            </select>
          </label>
          <span className="flex-1" />
          <Button asChild size="sm">
            <a href={exportHref(workspace.id, { from: filters.from, group_id: filters.groupId, account_id: filters.accountId })} download>
              Export CSV
            </a>
          </Button>
        </div>

        <ul className="grid grid-cols-7 gap-2 max-[1100px]:grid-cols-4 max-rail:grid-cols-2" aria-label="Headline figures">
          {TILES.map((k) => (
            <li key={k} className="grid gap-1 rounded-md border border-border bg-raised p-3">
              <span className="font-label text-caption tracking-[0.06em] uppercase text-fg-subtle">{METRIC_LABEL[k]}</span>
              <span className={`font-mono text-lg ${totals[k] === null || totals[k] === undefined ? "text-fg-subtle text-sm" : ""}`}>
                {formatMetric(totals[k], k)}
              </span>
            </li>
          ))}
        </ul>

        <Panel>
          <PanelHead
            title={`${METRIC_LABEL[series.metric] ?? series.metric} by day`}
            actions={
              <SegmentedControl
                aria-label="Metric"
                value={series.metric}
                onValueChange={(m) => void pick(m)}
                options={CHART_METRICS.map((m) => ({ value: m, label: METRIC_LABEL[m], disabled: loading }))}
              />
            }
          />
          <PanelBody>
            <BarChart points={series.points} metric={series.metric} label={`${METRIC_LABEL[series.metric] ?? series.metric}, by publish day`} />
          </PanelBody>
        </Panel>

        <div className="grid gap-3.5 lg:grid-cols-2">
          <Panel>
            <PanelHead title="Accounts against their own baseline" />
            <PanelBody className="p-0">
              <table className="w-full text-sm">
                <thead className="text-left text-caption text-fg-subtle">
                  <tr>
                    <th scope="col" className="px-3.5 py-2 font-medium">Account</th>
                    <th scope="col" className="px-3.5 py-2 text-right font-medium">Posts</th>
                    <th scope="col" className="px-3.5 py-2 text-right font-medium">Median engagement</th>
                    <th scope="col" className="px-3.5 py-2 text-right font-medium">vs usual</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {p.accountsCmp.map((r) => (
                    <tr key={String(r.social_account_id)}>
                      <td className="px-3.5 py-2">
                        {String(r.nickname)} <span className="text-caption text-fg-subtle">{platformLabel(String(r.platform) as never)}</span>
                      </td>
                      <td className="px-3.5 py-2 text-right font-mono">{String(r.posts)}</td>
                      <td className="px-3.5 py-2 text-right font-mono">{formatMetric(r.median_engagement as number | null)}</td>
                      <td className="px-3.5 py-2 text-right">
                        <Tag tone={indexTone(r.index_vs_baseline as number | null)}>{formatIndex(r.index_vs_baseline as number | null)}</Tag>
                      </td>
                    </tr>
                  ))}
                  {p.accountsCmp.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-3.5 py-3 text-fg-subtle">No published posts in this window yet.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
              <p className="px-3.5 py-2 text-caption text-fg-subtle">
                Each account is compared with its own median over the 90 days before this window, so a small account doing
                well is not ranked below a large one doing badly.
              </p>
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHead title="Approval turnaround" />
            <PanelBody className="grid gap-2">
              <p className="text-sm">
                <span className="font-mono text-lg">{formatMinutes(turn.median_minutes)}</span>{" "}
                <span className="text-fg-muted">median from request to first decision</span>
              </p>
              <p className="text-caption text-fg-subtle">
                90th percentile {formatMinutes(turn.p90_minutes)} · {turn.count} decided · {turn.undecided} still open
              </p>
              <ul className="grid gap-1 text-sm">
                {(turn.by_group ?? []).map((g) => (
                  <li key={String(g.group)} className="flex justify-between">
                    <span>{String(g.group)}</span>
                    <span className="font-mono">{formatMinutes(g.median_minutes as number | null)}</span>
                  </li>
                ))}
              </ul>
              <h3 className="mt-2 font-label text-caption font-semibold tracking-[0.09em] uppercase text-fg-subtle">Groups</h3>
              <ul className="grid gap-1 text-sm">
                {p.groupsCmp.map((g) => (
                  <li key={String(g.account_group_id)} className="flex items-center gap-2">
                    <GroupDot group={String(g.account_group_id)} /> {String(g.name)}
                    <span className="flex-1" />
                    <span className="text-caption text-fg-subtle">{String(g.posts)} posts</span>
                    <Tag tone={indexTone(g.index_vs_baseline as number | null)}>{formatIndex(g.index_vs_baseline as number | null)}</Tag>
                  </li>
                ))}
              </ul>
            </PanelBody>
          </Panel>
        </div>

        <Panel>
          <PanelHead title="Top posts" />
          {p.top.length === 0 ? (
            <PanelBody>
              <Notice tone="info">Metrics arrive about an hour after a post is published.</Notice>
            </PanelBody>
          ) : (
            <ul className="divide-y divide-border">
              {p.top.map((t) => {
                const m = t.metrics as Record<string, number | null>;
                return (
                  <li key={String(t.published_post_id)} className="grid gap-1 px-3.5 py-2.5 text-sm">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{String(t.account_nickname)}</span>
                      <span className="text-caption text-fg-subtle">
                        {platformLabel(String(t.platform) as never)} · <Time value={String(t.published_at)} tz={workspace.timezone} format="datetime" />
                      </span>
                      <span className="flex-1" />
                      {t.permalink ? (
                        <a className="text-caption text-accent hover:underline" href={String(t.permalink)} target="_blank" rel="noreferrer">
                          View
                        </a>
                      ) : null}
                    </span>
                    <span className="line-clamp-2 text-fg-muted">{String(t.excerpt)}</span>
                    <span className="flex flex-wrap gap-3 font-mono text-caption">
                      {(["impressions", "likes", "comments", "shares"] as const).map((k) => (
                        <span key={k} className={m[k] === null ? "text-fg-subtle" : ""}>
                          {METRIC_LABEL[k]} {m[k] === null ? NOT_REPORTED : formatMetric(m[k])}
                        </span>
                      ))}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
        <InsightsPanels workspaceId={workspace.id} timezone={workspace.timezone} canTune={workspace.role === "owner" || workspace.role === "admin"} />
      </main>
    </>
  );
}
