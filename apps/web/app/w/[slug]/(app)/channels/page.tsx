import { channelStatus, listBindings } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ChannelsScreen } from "@/components/channels/channels-screen";
import { getSession, serverClient, unwrap } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isAdminRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.channels.title };

/* Approval Channels (M7): Telegram status and on/off, pairing, paired approvers. */
export default async function ChannelsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/channels`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const client = await serverClient();
  const path = { workspace_id: workspace.id };
  const [status, bindings] = await Promise.all([
    unwrap(channelStatus({ client, path }), here),
    unwrap(listBindings({ client, path }), here),
  ]);
  return (
    <ChannelsScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone, archived: workspace.archived }}
      status={status}
      bindings={bindings.data}
      canManage={isAdminRole(workspace.role)}
    />
  );
}
