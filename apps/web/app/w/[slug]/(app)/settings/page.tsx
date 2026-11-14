import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getSecondFactorPolicy, mediaSpend, securityStatus } from "@mera/sdk";
import { ApiKeysPanel } from "@/components/settings/api-keys-panel";
import { DigestPanel, ExportsPanel, MyDataPanel, SecurityAlertsPanel, WebhooksPanel } from "@/components/settings/integrations-panels";
import { LLMSettingsPanel } from "@/components/settings/llm-settings-panel";
import { MediaSpendPanel } from "@/components/settings/media-spend-panel";
import { SecondFactorPanel } from "@/components/settings/second-factor-panel";
import { DangerZonePanel, WorkspaceDefaultsPanel, WorkspaceProfilePanel } from "@/components/settings/workspace-panels";
import { Topbar } from "@/components/shell/topbar";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { getApiKeys, getSession, getWorkspaceDetail, optional, serverClient, unwrap } from "@/lib/api/server";
import { readLevelSettings } from "@/lib/level-settings";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isAdminRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.settings.title };

/*
 * Settings (settings.html). Live: workspace profile and defaults (Admin+),
 * archive / restore (Admin+), delete (Owner), API keys (Admin+). Media
 * generation spend and limits: P2-M5. Phase 3: your digest, webhooks, exports
 * and security alerts (Admin+), and your own data (P3-M4, P3-M7).
 */
export default async function SettingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/settings`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  const isAdmin = isAdminRole(workspace.role);
  const client = await serverClient();
  const path = { workspace_id: workspace.id };
  const [detail, keys, sfPolicy, spend, security] = await Promise.all([
    getWorkspaceDetail(workspace.id, here),
    isAdmin ? getApiKeys(workspace.id, here) : Promise.resolve([]),
    optional(unwrap(getSecondFactorPolicy({ client, path }), here)),
    optional(unwrap(mediaSpend({ client, path }), here)),
    optional(unwrap(securityStatus({ client }), here)),
  ]);
  // Validated before use: the stored JSON is the API's, but the form trusts nothing it renders.
  const rawSettings: unknown = detail.settings;
  const settings = rawSettings && typeof rawSettings === "object" ? readLevelSettings(rawSettings) : null;
  const view = {
    id: detail.id,
    name: detail.name,
    slug: detail.slug,
    timezone: detail.timezone,
    workspace_type: detail.workspace_type,
    archived: detail.archived,
  };

  return (
    <>
      <Topbar title={SCREENS.settings.title} sub={`${workspace.name} workspace`} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <WorkspaceProfilePanel workspace={view} canEdit={isAdmin} />
        <WorkspaceDefaultsPanel workspace={view} settings={settings} canEdit={isAdmin} />
        <LLMSettingsPanel workspaceId={workspace.id} canEdit={isAdmin} />
        {sfPolicy ? (
          <SecondFactorPanel
            workspaceId={workspace.id}
            policy={sfPolicy.data}
            canEdit={isAdmin && !workspace.archived}
            isOwner={workspace.role === "owner"}
          />
        ) : null}

        {spend ? <MediaSpendPanel workspaceId={workspace.id} spend={spend} canEdit={isAdmin && !workspace.archived} /> : null}

        {isAdmin ? (
          <ApiKeysPanel workspaceId={workspace.id} keys={keys} tz={workspace.timezone} archived={workspace.archived} />
        ) : (
          <Panel>
            <PanelHead title="API keys" />
            <PanelBody>
              <Notice>Only owners and admins can create or revoke API keys for this workspace.</Notice>
            </PanelBody>
          </Panel>
        )}

        <DigestPanel workspaceId={workspace.id} timezone={workspace.timezone} />

        {isAdmin ? (
          <>
            <WebhooksPanel workspaceId={workspace.id} archived={workspace.archived} />
            <ExportsPanel workspaceId={workspace.id} timezone={workspace.timezone} />
            <SecurityAlertsPanel workspaceId={workspace.id} timezone={workspace.timezone} />
          </>
        ) : null}

        <DangerZonePanel workspace={view} canArchive={isAdmin} canDelete={workspace.role === "owner"} />

        {security ? (
          <MyDataPanel passwordSet={security.password_set !== false} mfaEnabled={Boolean(security.mfa_enabled)} />
        ) : null}
      </main>
    </>
  );
}
