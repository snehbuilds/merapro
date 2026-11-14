import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { CommandPalette } from "@/components/shell/command-palette";
import { Rail } from "@/components/shell/rail";
import { SecondFactorProvider } from "@/components/mera/second-factor";
import { ShellProvider } from "@/components/shell/shell-provider";
import { SkipLink } from "@/components/shell/skip-link";
import { Notice } from "@/components/ui/notice";
import { getAccounts, getApprovalRequests, getSession, optional } from "@/lib/api/server";
import { liveAccounts } from "@/lib/accounts";
import { findBySlug, toShellUser, toShellWorkspace } from "@/lib/workspace";

/* mera.css .app: rail column + main column; one column at ≤900px. */
export default async function AppShellLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const session = await getSession(`/w/${slug}`);
  const workspaces = session.workspaces.map(toShellWorkspace);
  // Only the user's own workspaces resolve; anything else is indistinguishable from missing.
  const workspace = findBySlug(workspaces, slug);
  if (!workspace) notFound();

  // The rail's "Connected" badge. A failed fetch drops the badge, not the page.
  const [accounts, open] = await Promise.all([
    optional(getAccounts(workspace.id, `/w/${slug}`)),
    optional(getApprovalRequests(workspace.id, { state: "open", limit: 100 }, `/w/${slug}`)),
  ]);
  // Rail badges: live accounts, and accounts still waiting for a decision.
  const counts = {
    ...(accounts ? { accounts: liveAccounts(accounts).length } : {}),
    ...(open ? { approvals: open.reduce((n, r) => n + r.variants.filter((v) => v.state === "pending").length, 0) } : {}),
  };

  return (
    <ShellProvider workspace={workspace} workspaces={workspaces} user={toShellUser(session.user)} counts={counts}>
      <SecondFactorProvider workspaceId={workspace.id} slug={workspace.slug}>
        <SkipLink />
        <div className="grid min-h-screen grid-cols-[var(--rail-w)_minmax(0,1fr)] max-rail:grid-cols-[minmax(0,1fr)]">
          <Rail />
          <div className="flex min-w-0 flex-col">
            {workspace.archived ? (
              <Notice tone="warn" title="This workspace is archived." className="m-4 mb-0 max-rail:m-3 max-rail:mb-0" role="status">
                Everything is read-only: nothing can be created, changed or published until an owner restores it.
              </Notice>
            ) : null}
            {children}
          </div>
        </div>
        <CommandPalette />
      </SecondFactorProvider>
    </ShellProvider>
  );
}
