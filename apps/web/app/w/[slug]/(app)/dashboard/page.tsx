import { calendar, listDeadLetters } from "@mera/sdk";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { GroupDot } from "@/components/mera/group-dot";
import { AttentionNotices } from "@/components/shell/attention-notices";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Time } from "@/components/ui/time";
import { dashboardAttention } from "@/lib/accounts";
import { getAccounts, getApprovalRequests, getNotifications, getSession, optional, serverClient, unwrap } from "@/lib/api/server";
import { aroundNow, asScheduledState, publishStateLabel } from "@/lib/calendar";
import { SCREENS } from "@/lib/nav";
import { findBySlug } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.dashboard.title };

/*
 * Dashboard (dashboard.html). What needs you (accounts needing reauth, failed posts,
 * approvals waiting), what goes out next, and what went out in the last day, per account.
 */
export default async function DashboardPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/dashboard`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const client = await serverClient();
  const window = aroundNow(1, 2);

  const [accounts, notifications, open, slots, dead] = await Promise.all([
    optional(getAccounts(workspace.id, here)),
    optional(getNotifications(workspace.id, { unreadOnly: true }, here)),
    optional(getApprovalRequests(workspace.id, { state: "open", limit: 20 }, here)),
    optional(unwrap(calendar({ client, path: { workspace_id: workspace.id }, query: window }), here)),
    optional(unwrap(listDeadLetters({ client, path: { workspace_id: workspace.id } }), here)),
  ]);
  const attention = dashboardAttention(accounts ?? [], notifications ?? []);
  const screen = SCREENS.dashboard;
  const items = slots?.data ?? [];
  const published = items.filter((s) => s.state === "published").reverse();
  const upcoming = items.filter((s) => s.state === "queued" || s.state === "failed" || s.state === "publishing");
  const waiting = (open ?? []).reduce((n, r) => n + r.variants.filter((v) => v.can_decide).length, 0);
  const failing = (dead?.data ?? []).length;
  const tz = workspace.timezone;

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
        <div className="grid gap-3.5 lg:grid-cols-3">
          <Panel>
            <PanelHead title="Awaiting your approval" />
            <PanelBody className="grid gap-2">
              <p className="font-mono text-2xl">{waiting}</p>
              <p className="text-sm text-fg-muted">
                {waiting === 1 ? "account is" : "accounts are"} waiting for your decision across {(open ?? []).length} post
                {(open ?? []).length === 1 ? "" : "s"}.
              </p>
              <Button asChild size="sm" variant={waiting ? "primary" : "default"} className="justify-self-start">
                <Link href={`/w/${slug}/approvals`}>Open the queue</Link>
              </Button>
            </PanelBody>
          </Panel>
          <Panel className="lg:col-span-2">
            <PanelHead title="Up next" />
            <ul className="divide-y divide-border">
              {upcoming.length === 0 ? <li className="px-3.5 py-3 text-sm text-fg-subtle">Nothing scheduled in the next two days.</li> : null}
              {upcoming.slice(0, 8).map((s) => (
                <li key={s.id} className="flex flex-wrap items-center gap-2 px-3.5 py-2 text-sm">
                  <Time value={s.scheduled_for} tz={tz} format="datetime" className="font-mono text-caption" />
                  {s.account_group_id ? <GroupDot group={s.account_group_id} /> : null}
                  <span className="font-medium">{s.account_nickname}</span>
                  <span className="min-w-0 flex-1 truncate text-fg-muted">{s.excerpt}</span>
                  <StatusPill kind="scheduled_post" state={asScheduledState(s.state)} label={publishStateLabel(s.state, s.attempt_count, s.max_attempts)} />
                </li>
              ))}
            </ul>
          </Panel>
        </div>
        <Panel>
          <PanelHead title="Published in the last day" />
          <ul className="divide-y divide-border">
            {published.length === 0 ? <li className="px-3.5 py-3 text-sm text-fg-subtle">Nothing published yet today.</li> : null}
            {published.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-2 px-3.5 py-2 text-sm">
                <Time value={s.published_at ?? s.scheduled_for} tz={tz} format="time" className="font-mono text-caption" />
                <span className="font-medium">{s.account_nickname}</span>
                <span className="min-w-0 flex-1 truncate text-fg-muted">{s.post_set_title}</span>
                {s.permalink ? (
                  <a className="text-caption text-accent hover:underline" href={s.permalink} target="_blank" rel="noreferrer">
                    View post
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        </Panel>
      </main>
    </>
  );
}
