import { auditLog, listCalls, listClients, listServers } from "@mera/sdk";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { McpScreen } from "@/components/mcp/mcp-screen";
import { RegistryPanel, type RegistryCall, type RegistryServer } from "@/components/mcp/registry-panel";
import { WorkspaceAccessNotice } from "@/components/security/workspace-access-notice";
import { getSession, getWorkspaceDetail, serverClient } from "@/lib/api/server";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isAdminRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.mcp.title };

function resolveMcpEndpoint(): string {
  if (process.env.MERA_PUBLIC_MCP_URL && !process.env.MERA_PUBLIC_MCP_URL.includes("localhost")) {
    return process.env.MERA_PUBLIC_MCP_URL;
  }
  if (process.env.MCP_RESOURCE_URL && !process.env.MCP_RESOURCE_URL.includes("localhost")) {
    return process.env.MCP_RESOURCE_URL;
  }
  const publicApi = process.env.MERA_PUBLIC_API_URL || process.env.MERA_API_URL;
  if (publicApi && !publicApi.includes("localhost") && !publicApi.includes("127.0.0.1")) {
    try {
      const fixed = publicApi.startsWith("http") ? publicApi : `https://${publicApi}`;
      const url = new URL(fixed);
      return `${url.origin}/mcp`;
    } catch {
      // ignore
    }
  }
  const publicUrl = process.env.MERA_PUBLIC_URL;
  if (publicUrl && !publicUrl.includes("localhost") && !publicUrl.includes("127.0.0.1")) {
    try {
      const fixed = publicUrl.startsWith("http") ? publicUrl : `https://${publicUrl}`;
      const url = new URL(fixed);
      return `${url.origin}/mcp`;
    } catch {
      // ignore
    }
  }
  return process.env.MERA_PUBLIC_MCP_URL || "https://mcp.drtea.cloud/mcp";
}

/* MCP: Mera as a server (P2-M8: clients, how to connect, recent calls) and the external
   servers its agents may use (P2-M9: registry, review, waiting tool calls). */
export default async function McpPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const here = `/w/${slug}/mcp`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();
  const client = await serverClient();
  const path = { workspace_id: workspace.id };
  const assignedAdmin = isAdminRole(workspace.role);
  const accessCall = assignedAdmin
    ? getWorkspaceDetail(workspace.id, here).then((detail) => isAdminRole(detail.role))
    : Promise.resolve(false);
  const [clients, calls, servers, pending, admin] = await Promise.all([
    listClients({ client, path }),
    accessCall.then(async (allowed) => allowed ? auditLog({ client, path, query: { surface: "mcp", limit: 20 } }) : undefined),
    listServers({ client, path }),
    listCalls({ client, path, query: { state: "pending_approval" } }),
    accessCall,
  ]);
  const registry = ((servers.data as { data?: RegistryServer[] } | undefined)?.data ?? []) as RegistryServer[];
  const waiting = ((pending.data as { data?: RegistryCall[] } | undefined)?.data ?? []) as RegistryCall[];
  return (
    <McpScreen
      workspace={{ id: workspace.id, slug: workspace.slug, timezone: workspace.timezone }}
      clients={clients.data?.data ?? []}
      calls={calls?.data?.data ?? []}
      endpoint={resolveMcpEndpoint()}
      canManage={admin}
    >
      {assignedAdmin && !admin ? <WorkspaceAccessNotice slug={slug} /> : null}
      <RegistryPanel
        workspace={{ id: workspace.id, timezone: workspace.timezone }}
        servers={registry}
        calls={waiting}
        canManage={admin && !workspace.archived}
        canDecide={workspace.role !== "viewer"}
      />
    </McpScreen>
  );
}
