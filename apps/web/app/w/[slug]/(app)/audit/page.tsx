import { auditLog } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AuditScreen } from "@/components/audit/audit-screen";
import { WorkspaceAccessNotice } from "@/components/security/workspace-access-notice";
import { Topbar } from "@/components/shell/topbar";
import { Notice } from "@/components/ui/notice";
import { ApiRequestError, getAccounts, getSession, optional, serverClient, unwrap } from "@/lib/api/server";
import { auditAction } from "@/lib/audit";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isAdminRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.audit.title };

/* Audit Log (M11): Admins. ?action=<prefix>&account=<id>. */
export default async function AuditPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ action?: string; account?: string }>;
}) {
  const { slug } = await params;
  const q = await searchParams;
  const here = `/w/${slug}/audit`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  if (!isAdminRole(workspace.role)) {
    return (
      <>
        <Topbar title={SCREENS.audit.title} />
        <main id="main" tabIndex={-1} className="p-6 outline-none">
          <Notice tone="info">The audit log is for Admins and Owners.</Notice>
        </main>
      </>
    );
  }
  const action = auditAction(q.action);
  const accountId = q.account && /^[0-9a-f-]{36}$/.test(q.account) ? q.account : null;
  const client = await serverClient();
  const [page, accounts] = await Promise.all([
    unwrap(
      auditLog({ client, path: { workspace_id: workspace.id }, query: { limit: 50, action: action || null, account_id: accountId } }),
      here,
    ).catch((error: unknown) => {
      if (error instanceof ApiRequestError && error.apiError.status === 403) return null;
      throw error;
    }),
    optional(getAccounts(workspace.id, here)),
  ]);
  if (!page) {
    return (
      <>
        <Topbar title={SCREENS.audit.title} />
        <main id="main" tabIndex={-1} className="p-6 outline-none">
          <WorkspaceAccessNotice slug={slug} />
        </main>
      </>
    );
  }
  return (
    <AuditScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone }}
      entries={page.data}
      action={action}
      accountId={accountId}
      accounts={(accounts ?? []).map((a) => ({ id: a.id, nickname: a.nickname }))}
    />
  );
}
