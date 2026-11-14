import { inboxSummary, listComments } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { InboxScreen } from "@/components/inbox/inbox-screen";
import { getAccounts, getGroups, getSession, serverClient, unwrap } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isEditorRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.inbox.title };

const FILTERS = ["state", "platform", "social_account_id", "account_group_id", "sentiment", "intent"] as const;

/* Unified Inbox (P2-M6): comments on published posts, triage and replies. */
export default async function InboxPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const sp = await searchParams;
  const here = `/w/${slug}/inbox`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const filters: Record<string, string> = {};
  for (const k of FILTERS) {
    const v = sp[k];
    if (typeof v === "string" && v.length <= 64) filters[k] = v;
  }
  const client = await serverClient();
  const path = { workspace_id: workspace.id };
  const [comments, summary, accounts, groups] = await Promise.all([
    unwrap(listComments({ client, path, query: { ...filters, limit: 100 } }), here),
    unwrap(inboxSummary({ client, path }), here),
    getAccounts(workspace.id, here),
    getGroups(workspace.id, here),
  ]);
  const role = workspace.role;
  return (
    <InboxScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone, archived: workspace.archived }}
      comments={comments.data}
      summary={summary}
      accounts={accounts.map((a) => ({ id: a.id, nickname: a.nickname, platform: a.platform }))}
      groups={groups.map((g) => ({ id: g.id, name: g.name }))}
      filters={filters}
      canReply={isEditorRole(role)}
      canApprove={role !== "viewer"}
    />
  );
}
