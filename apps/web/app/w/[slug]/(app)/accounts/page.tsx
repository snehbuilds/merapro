import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountsScreen } from "@/components/accounts/accounts-screen";
import { parseConnectReturn } from "@/lib/accounts";
import { getAccounts, getGroups, getSession } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isAdminRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.accounts.title };

/*
 * Connected Accounts (accounts.html). Live since Sprint 4 (M3): list, health,
 * capabilities, connect / reconnect / disconnect (Admin+), per-account
 * defaults. The OAuth callback lands here with ?connected= or ?error=.
 */
export default async function AccountsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const query = await searchParams;
  const here = `/w/${slug}/accounts`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const [accounts, groups] = await Promise.all([getAccounts(workspace.id, here), getGroups(workspace.id, here)]);
  const selected = typeof query.account === "string" ? query.account : undefined;

  return (
    <AccountsScreen
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
        timezone: workspace.timezone,
        archived: workspace.archived,
      }}
      accounts={accounts}
      groups={groups}
      canManage={isAdminRole(workspace.role)}
      connectReturn={parseConnectReturn(query)}
      initialAccountId={selected}
    />
  );
}
