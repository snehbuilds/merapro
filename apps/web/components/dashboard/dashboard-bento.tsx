import Link from "next/link";
import { Icon } from "@/components/icons";
import type { Platform as MarkPlatform } from "@/components/icons/paths";
import { PlatformMark } from "@/components/icons/platform-mark";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Time } from "@/components/ui/time";
import { platformLabel } from "@/lib/accounts";
import { formatMetric, type Point } from "@/lib/analytics";
import { localDate } from "@/lib/calendar";

export interface DashboardSlot {
  id: string;
  state: string;
  scheduled_for: string;
  published_at: string | null;
  account_nickname: string;
  platform: string;
  excerpt: string;
  post_set_title: string;
  permalink: string | null;
}

export interface DashboardAccount {
  id: string;
  platform: string;
  nickname: string;
  status: string;
}

export interface DashboardTopPost {
  published_post_id: string;
  platform: string;
  account_nickname: string;
  published_at: string;
  excerpt: string;
  permalink: string | null;
  metrics: Record<string, number | null>;
}

export interface DashboardSuggestion {
  id: string;
  title: string;
  subtitle: string;
  href: string;
}

export interface DashboardActivity {
  id: string;
  kind: "published" | "scheduled" | "failed" | "event";
  label: string;
  detail: string;
  at: string;
}

export interface DashboardBentoProps {
  slug: string;
  firstName: string;
  timezone: string;
  waitingApprovals: number;
  scheduledCount: number;
  published7d: number | null;
  engagements7d: number | null;
  reach7d: number | null;
  todayCount: number;
  tomorrowCount: number;
  weekCount: number;
  weekDays: { date: string; dow: string; dayNum: string; isToday: boolean; dots: ("published" | "queued" | "failed")[] }[];
  monthLabel: string;
  accounts: DashboardAccount[];
  reachSeries: Point[];
  engagementSeries: Point[];
  topPosts: DashboardTopPost[];
  suggestions: DashboardSuggestion[];
  activities: DashboardActivity[];
}

function asMarkPlatform(p: string): MarkPlatform | null {
  if (p === "linkedin_member" || p === "linkedin_organization" || p === "linkedin") return "linkedin";
  if (p === "x" || p === "facebook" || p === "instagram" || p === "threads" || p === "bluesky" || p === "mastodon") {
    return p;
  }
  return null;
}

function greetingFor(tz: string): string {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", hourCycle: "h23" }).format(new Date()),
  );
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function shortDate(isoDate: string): string {
  const [, m = "1", d = "1"] = isoDate.split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[Math.max(0, Number(m) - 1)] ?? ""} ${Number(d)}`;
}

function DualLineChart({ reach, engagement }: { reach: Point[]; engagement: Point[] }) {
  const len = Math.max(reach.length, engagement.length, 2);
  const w = 520;
  const h = 156;
  const padL = 36;
  const padR = 12;
  const padT = 12;
  const padB = 26;
  const plotW = w - padL - padR;
  const plotH = h - padT - padB;

  const rVals = reach.map((p) => p.value ?? 0);
  const eVals = engagement.map((p) => p.value ?? 0);
  const maxVal = Math.max(10, ...rVals, ...eVals);

  const toCoords = (vals: number[]) =>
    vals.map((v, i) => {
      const x = padL + (vals.length <= 1 ? plotW / 2 : (i / (vals.length - 1)) * plotW);
      const y = padT + plotH - (v / maxVal) * plotH;
      return [x, y] as const;
    });

  const linePath = (coords: readonly (readonly [number, number])[]) =>
    coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

  const areaPath = (coords: readonly (readonly [number, number])[]) => {
    if (coords.length === 0) return "";
    const base = padT + plotH;
    return `${linePath(coords)} L${coords[coords.length - 1]![0].toFixed(1)},${base} L${coords[0]![0].toFixed(1)},${base} Z`;
  };

  const rCoords = toCoords(rVals);
  const eCoords = toCoords(eVals);
  const ticks = [0, 0.5, 1].map((t) => ({
    y: padT + plotH - t * plotH,
    label: formatMetric(Math.round(maxVal * t)),
  }));
  const xLabels = (reach.length >= engagement.length ? reach : engagement).map((p) => shortDate(p.date));

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full overflow-visible" role="img" aria-label="7-day reach and engagement chart">
      <defs>
        <linearGradient id="mera-reach-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.22" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {ticks.map((t) => (
        <g key={t.y}>
          <line x1={padL} x2={w - padR} y1={t.y} y2={t.y} stroke="var(--border)" strokeDasharray="3 3" />
          <text x={padL - 6} y={t.y + 3} textAnchor="end" className="fill-fg-subtle font-mono text-[10px]">
            {t.label}
          </text>
        </g>
      ))}
      {rCoords.length > 1 ? <path d={areaPath(rCoords)} fill="url(#mera-reach-grad)" /> : null}
      {rCoords.length > 1 ? (
        <path d={linePath(rCoords)} fill="none" stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      ) : null}
      {eCoords.length > 1 ? (
        <path d={linePath(eCoords)} fill="none" stroke="#0ea5e9" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      ) : null}
      {rCoords.map(([x, y], i) => (
        <circle key={`r-${i}`} cx={x} cy={y} r="2.8" fill="var(--accent)" stroke="var(--card)" strokeWidth="1.2" />
      ))}
      {eCoords.map(([x, y], i) => (
        <circle key={`e-${i}`} cx={x} cy={y} r="2.5" fill="#0ea5e9" stroke="var(--card)" strokeWidth="1.2" />
      ))}
      {xLabels.map((lbl, i) => {
        const x = padL + (len <= 1 ? plotW / 2 : (i / (len - 1)) * plotW);
        return (
          <text key={`${lbl}-${i}`} x={x} y={h - 4} textAnchor="middle" className="fill-fg-subtle font-mono text-[10px]">
            {lbl}
          </text>
        );
      })}
    </svg>
  );
}

export function DashboardBento(p: DashboardBentoProps) {
  const greeting = greetingFor(p.timezone);
  const base = `/w/${p.slug}`;

  return (
    <div className="grid gap-3.5">
      {/* Greeting Banner */}
      <section className="relative overflow-hidden rounded-xl border border-border bg-gradient-to-r from-[color-mix(in_srgb,var(--accent)_7%,var(--card))] via-card to-[color-mix(in_srgb,#0ea5e9_8%,var(--card))] px-5 py-4 shadow-card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-lg font-medium tracking-tight text-fg">
              {greeting}, {p.firstName}
            </h2>
            <p className="text-sm text-fg-muted">Here&apos;s what&apos;s happening with your content today.</p>
          </div>
          <span className="rounded-full border border-border bg-card/80 px-2.5 py-1 font-mono text-[11px] text-fg-subtle">
            {p.timezone}
          </span>
        </div>
      </section>

      {/* Top 5 KPI Bento Cards */}
      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" aria-label="Key metrics">
        <Link href={`${base}/calendar`} className="group rounded-xl border border-border bg-card p-3.5 shadow-card transition-colors hover:border-border-strong">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[color-mix(in_srgb,var(--accent)_12%,transparent)] text-accent">
              <Icon name="calendar" size="sm" />
            </span>
            <div className="min-w-0">
              <p className="font-mono text-xl font-semibold leading-tight">{p.scheduledCount}</p>
              <p className="text-caption text-fg-muted">Scheduled</p>
            </div>
          </div>
        </Link>

        <Link href={`${base}/approvals`} className="group rounded-xl border border-border bg-card p-3.5 shadow-card transition-colors hover:border-border-strong">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[color-mix(in_srgb,#0ea5e9_12%,transparent)] text-[#0284c7]">
              <Icon name="approvals" size="sm" />
            </span>
            <div className="min-w-0">
              <p className="font-mono text-xl font-semibold leading-tight">{p.waitingApprovals}</p>
              <p className="text-caption text-fg-muted">In approval</p>
            </div>
          </div>
        </Link>

        <Link href={`${base}/analytics`} className="group rounded-xl border border-border bg-card p-3.5 shadow-card transition-colors hover:border-border-strong">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[color-mix(in_srgb,var(--ok)_12%,transparent)] text-ok">
              <Icon name="check" size="sm" />
            </span>
            <div className="min-w-0">
              <p className="font-mono text-xl font-semibold leading-tight">{formatMetric(p.published7d)}</p>
              <p className="text-caption text-fg-muted">Published (7d)</p>
            </div>
          </div>
        </Link>

        <Link href={`${base}/analytics`} className="group rounded-xl border border-border bg-card p-3.5 shadow-card transition-colors hover:border-border-strong">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[color-mix(in_srgb,#8b5cf6_12%,transparent)] text-[#7c3aed]">
              <Icon name="inbox" size="sm" />
            </span>
            <div className="min-w-0">
              <p className="font-mono text-xl font-semibold leading-tight">{formatMetric(p.engagements7d)}</p>
              <p className="text-caption text-fg-muted">Engagements</p>
            </div>
          </div>
        </Link>

        <Link href={`${base}/analytics`} className="group rounded-xl border border-border bg-card p-3.5 shadow-card transition-colors hover:border-border-strong sm:col-span-2 lg:col-span-1">
          <div className="flex items-center gap-2.5">
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-[color-mix(in_srgb,var(--accent)_12%,transparent)] text-accent">
              <Icon name="analytics" size="sm" />
            </span>
            <div className="min-w-0">
              <p className="font-mono text-xl font-semibold leading-tight">{formatMetric(p.reach7d)}</p>
              <p className="text-caption text-fg-muted">Total reach</p>
            </div>
          </div>
        </Link>
      </section>

      {/* Row 1: Publishing Queue (4) + Content Calendar (5) + Connected Platforms (3) */}
      <section className="grid gap-3.5 lg:grid-cols-12">
        <Panel className="lg:col-span-4">
          <PanelHead
            title="Publishing queue"
            actions={
              <Link href={`${base}/calendar`} className="text-caption font-medium text-accent hover:underline">
                View all →
              </Link>
            }
          />
          <PanelBody flush>
            <ul className="divide-y divide-border text-sm">
              <li className="flex items-center justify-between px-3.5 py-2.5">
                <span className="text-fg-muted">Today</span>
                <span className="rounded-full bg-[color-mix(in_srgb,var(--accent)_12%,transparent)] px-2 py-0.5 font-mono text-caption font-medium text-accent">
                  {p.todayCount}
                </span>
              </li>
              <li className="flex items-center justify-between px-3.5 py-2.5">
                <span className="text-fg-muted">Tomorrow</span>
                <span className="rounded-full bg-[color-mix(in_srgb,#0ea5e9_12%,transparent)] px-2 py-0.5 font-mono text-caption font-medium text-[#0284c7]">
                  {p.tomorrowCount}
                </span>
              </li>
              <li className="flex items-center justify-between px-3.5 py-2.5">
                <span className="text-fg-muted">Next 7 days</span>
                <span className="rounded-full bg-paper px-2 py-0.5 font-mono text-caption font-medium text-fg-muted">
                  {p.weekCount}
                </span>
              </li>
            </ul>
          </PanelBody>
        </Panel>

        <Panel className="lg:col-span-5">
          <PanelHead
            title="Content calendar"
            actions={<span className="font-mono text-caption text-fg-muted">{p.monthLabel}</span>}
          />
          <PanelBody className="flex h-[calc(100%-42px)] items-center">
            <div className="grid w-full grid-cols-7 gap-1.5 text-center">
              {p.weekDays.map((d) => (
                <Link
                  key={d.date}
                  href={`${base}/calendar`}
                  className={
                    d.isToday
                      ? "flex flex-col items-center justify-between rounded-lg bg-accent px-1 py-2 text-white shadow-sm"
                      : "flex flex-col items-center justify-between rounded-lg px-1 py-2 transition-colors hover:bg-paper"
                  }
                >
                  <span className={d.isToday ? "text-[11px] font-medium text-white/85" : "text-[11px] text-fg-subtle"}>{d.dow}</span>
                  <span className="mt-1 font-mono text-sm font-semibold">{d.dayNum}</span>
                  <span className="mt-1.5 flex h-2 items-center gap-0.5">
                    {d.dots.slice(0, 3).map((dot, idx) => (
                      <span
                        key={idx}
                        className={
                          d.isToday
                            ? "size-1.5 rounded-full bg-white"
                            : dot === "published"
                              ? "size-1.5 rounded-full bg-ok"
                              : dot === "failed"
                                ? "size-1.5 rounded-full bg-danger"
                                : "size-1.5 rounded-full bg-accent"
                        }
                      />
                    ))}
                  </span>
                </Link>
              ))}
            </div>
          </PanelBody>
        </Panel>

        <Panel className="lg:col-span-3">
          <PanelHead
            title="Connected platforms"
            actions={
              <Link href={`${base}/accounts`} className="text-caption font-medium text-accent hover:underline">
                Manage →
              </Link>
            }
          />
          <PanelBody flush>
            {p.accounts.length === 0 ? (
              <p className="px-3.5 py-4 text-sm text-fg-subtle">No accounts connected yet.</p>
            ) : (
              <ul className="divide-y divide-border">
                {p.accounts.slice(0, 4).map((a) => {
                  const mark = asMarkPlatform(a.platform);
                  const ok = a.status === "connected" || a.status === "active";
                  return (
                    <li key={a.id} className="flex items-center gap-2 px-3.5 py-2 text-sm">
                      {mark ? <PlatformMark platform={mark} size="sm" decorative className="text-fg-muted" /> : null}
                      <span className="min-w-0 flex-1 truncate font-medium">{a.nickname}</span>
                      <StatusPill
                        kind="account"
                        state={ok ? "active" : "needs_reauth"}
                        label={ok ? "Connected" : "Reauth"}
                      />
                    </li>
                  );
                })}
              </ul>
            )}
          </PanelBody>
        </Panel>
      </section>

      {/* Row 2: Content Performance (6) + Top Performing Posts (6) */}
      <section className="grid gap-3.5 lg:grid-cols-12">
        <Panel className="lg:col-span-6">
          <PanelHead
            title="Content performance (last 7 days)"
            actions={
              <Link href={`${base}/analytics`} className="text-caption font-medium text-accent hover:underline">
                Details →
              </Link>
            }
          />
          <PanelBody className="grid gap-2">
            <DualLineChart reach={p.reachSeries} engagement={p.engagementSeries} />
            <div className="flex flex-wrap items-center justify-center gap-5 border-t border-border pt-2 text-caption text-fg-muted">
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-accent" />
                Reach <strong className="font-mono text-fg">{formatMetric(p.reach7d)}</strong>
              </span>
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-[#0ea5e9]" />
                Engagements <strong className="font-mono text-fg">{formatMetric(p.engagements7d)}</strong>
              </span>
            </div>
          </PanelBody>
        </Panel>

        <Panel className="lg:col-span-6">
          <PanelHead
            title="Top performing posts"
            actions={
              <Link href={`${base}/analytics`} className="text-caption font-medium text-accent hover:underline">
                View all →
              </Link>
            }
          />
          <PanelBody flush>
            {p.topPosts.length === 0 ? (
              <p className="px-3.5 py-5 text-sm text-fg-subtle">Metrics arrive about an hour after a post is published.</p>
            ) : (
              <ul className="divide-y divide-border">
                {p.topPosts.slice(0, 3).map((post) => {
                  const mark = asMarkPlatform(post.platform);
                  const imp = post.metrics.impressions ?? post.metrics.reach;
                  const eng = post.metrics.engagement ?? post.metrics.likes;
                  return (
                    <li key={post.published_post_id} className="flex items-center gap-3 px-3.5 py-2.5 text-sm">
                      <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-paper text-fg-muted">
                        {mark ? <PlatformMark platform={mark} size="sm" decorative /> : <Icon name="analytics" size="sm" />}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium text-fg">{post.excerpt || post.account_nickname}</p>
                        <p className="text-caption text-fg-subtle">
                          {platformLabel(post.platform as never)} · {shortDate(localDate(post.published_at, p.timezone))} ·{" "}
                          <span className="font-mono">{formatMetric(imp)}</span> reach ·{" "}
                          <span className="font-mono">{formatMetric(eng)}</span> eng
                        </p>
                      </div>
                      {post.permalink ? (
                        <a
                          href={post.permalink}
                          target="_blank"
                          rel="noreferrer"
                          className="shrink-0 text-caption font-medium text-accent hover:underline"
                        >
                          View
                        </a>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </PanelBody>
        </Panel>
      </section>

      {/* Row 3: AI Suggestions (6) + Recent Activity (6) */}
      <section className="grid gap-3.5 lg:grid-cols-12">
        <Panel className="lg:col-span-6">
          <PanelHead
            title="AI suggestions & trending"
            actions={
              <Link href={`${base}/discovery`} className="text-caption font-medium text-accent hover:underline">
                Discovery →
              </Link>
            }
          />
          <PanelBody flush>
            {p.suggestions.length === 0 ? (
              <p className="px-3.5 py-4 text-sm text-fg-subtle">No new suggestions yet. Add sources in Discovery to surface ideas.</p>
            ) : (
              <ul className="divide-y divide-border">
                {p.suggestions.slice(0, 3).map((s) => (
                  <li key={s.id}>
                    <Link href={s.href} className="flex items-center gap-3 px-3.5 py-2.5 text-sm transition-colors hover:bg-paper">
                      <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--warn)_14%,transparent)] text-warn">
                        <Icon name="discovery" size="sm" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium text-fg">{s.title}</p>
                        <p className="truncate text-caption text-fg-muted">{s.subtitle}</p>
                      </div>
                      <span className="text-fg-subtle">›</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </PanelBody>
        </Panel>

        <Panel className="lg:col-span-6">
          <PanelHead
            title="Recent activity"
            actions={
              <Link href={`${base}/calendar`} className="text-caption font-medium text-accent hover:underline">
                View all →
              </Link>
            }
          />
          <PanelBody flush>
            {p.activities.length === 0 ? (
              <p className="px-3.5 py-4 text-sm text-fg-subtle">Nothing published or scheduled in the last day.</p>
            ) : (
              <ul className="divide-y divide-border">
                {p.activities.slice(0, 4).map((act) => (
                  <li key={act.id} className="flex items-center gap-3 px-3.5 py-2.5 text-sm">
                    <span
                      className={
                        act.kind === "published"
                          ? "grid size-6 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--ok)_14%,transparent)] text-ok"
                          : act.kind === "failed"
                            ? "grid size-6 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--danger)_14%,transparent)] text-danger"
                            : "grid size-6 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--accent)_12%,transparent)] text-accent"
                      }
                    >
                      <Icon name={act.kind === "published" ? "check" : "calendar"} size="sm" />
                    </span>
                    <div className="min-w-0 flex-1 truncate">
                      <span className="font-medium text-fg">{act.label}</span>{" "}
                      <span className="text-fg-muted">— {act.detail}</span>
                    </div>
                    <Time value={act.at} tz={p.timezone} format="time" className="shrink-0 font-mono text-caption text-fg-subtle" />
                  </li>
                ))}
              </ul>
            )}
          </PanelBody>
        </Panel>
      </section>
    </div>
  );
}
