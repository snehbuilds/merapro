"use client";

import { revokeClient, type AuditEntryOut, type GrantOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { SCREENS } from "@/lib/nav";

/*
 * MCP (P2-M8.7): Mera as an MCP server for Claude and other clients. Connected clients
 * (OAuth grants, yours or everyone's for Admins) with revoke, how to connect, and the
 * calls made through MCP. External servers Mera itself uses are listed below them once
 * registered (P2-M9).
 */

export interface McpScreenProps {
  workspace: { id: string; slug: string; timezone: string };
  clients: GrantOut[];
  calls: AuditEntryOut[];
  endpoint: string;
  canManage: boolean;
  children?: ReactNode;
}

export function McpScreen({ workspace, clients, calls, endpoint, canManage, children }: McpScreenProps) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clientPage, setClientPage] = useState(0);
  const clientsPerPage = 2;
  const clientPageCount = Math.max(1, Math.ceil(clients.length / clientsPerPage));
  const activeClientPage = Math.min(clientPage, clientPageCount - 1);
  const visibleClients = clients.slice(activeClientPage * clientsPerPage, activeClientPage * clientsPerPage + clientsPerPage);
  const firstClient = clients.length === 0 ? 0 : activeClientPage * clientsPerPage + 1;
  const lastClient = Math.min((activeClientPage + 1) * clientsPerPage, clients.length);
  const apiUrl =
    endpoint && !endpoint.includes("YOUR-")
      ? endpoint.replace(/\/mcp\/?$/, "")
      : "https://api.drtea.cloud";
  const stdio = JSON.stringify(
    { mcpServers: { mera: { command: "python", args: ["-m", "mera.mcp"], env: { MERA_API_URL: apiUrl, MERA_API_KEY: "mera_sk_live_…" } } } },
    null,
    2,
  );

  async function revoke(id: string) {
    setBusy(id);
    setError(null);
    const r = await settle(revokeClient({ client: browserApi, path: { workspace_id: workspace.id, grant_id: id } }));
    setBusy(null);
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    router.refresh();
  }

  return (
    <>
      <Topbar title={SCREENS.mcp.title} sub={`${clients.length} connected client${clients.length === 1 ? "" : "s"}`} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        <div className="grid gap-3.5 lg:grid-cols-2">
          <Panel aria-labelledby="mcp-connect">
            <PanelHead title={<span id="mcp-connect">Use Mera from Claude</span>} />
            <PanelBody className="grid gap-3 text-sm">
              <p>
                Remote clients connect to <span className="font-mono rounded bg-surface-sunken px-1.5 py-0.5 text-xs text-accent font-medium select-all">{endpoint}</span> and sign in with OAuth: you choose the
                workspace and what they may do. A client can draft and send for approval; it can never approve, publish, resume
                automation or see a credential.
              </p>
              <div className="grid gap-1">
                <p className="text-caption font-semibold text-fg-muted">Claude Desktop on this machine (stdio, with an API key from Settings)</p>
                <pre className="overflow-x-auto rounded-md bg-surface-sunken p-2.5 font-mono text-mono-xs" aria-label="Claude Desktop configuration">
                  {stdio}
                </pre>
              </div>
            </PanelBody>
          </Panel>

          <Panel aria-labelledby="mcp-clients">
            <PanelHead title={<span id="mcp-clients">Connected clients</span>} />
            {clients.length === 0 ? (
              <PanelBody>
                <EmptyState icon="mcp" title="No clients connected" description="When you connect Claude or another MCP client, it appears here." tone="quiet" />
              </PanelBody>
            ) : (
              <>
                <ul className="divide-y divide-border" aria-label="Connected clients">
                  {visibleClients.map((c) => (
                    <li key={c.id} className="grid gap-1 px-3.5 py-2.5 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{c.client_name ?? c.client_id}</span>
                        <span className="flex-1" />
                        <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void revoke(c.id)} aria-label={`Disconnect ${c.client_name ?? c.client_id}`}>
                          {busy === c.id ? "Disconnecting…" : "Disconnect"}
                        </Button>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {c.scopes.map((s) => (
                          <Tag key={s} tone="outline">
                            {s}
                          </Tag>
                        ))}
                      </div>
                      <p className="text-caption text-fg-subtle">
                        Connected <Time value={c.created_at} tz={workspace.timezone} format="date" />
                        {c.last_used_at ? (
                          <>
                            {" "}
                            · last used <Time value={c.last_used_at} tz={workspace.timezone} format="datetime" />
                          </>
                        ) : null}
                      </p>
                    </li>
                  ))}
                </ul>
                {clients.length > clientsPerPage ? (
                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3.5 py-2.5 text-caption text-fg-muted">
                    <span>
                      Showing {firstClient}–{lastClient} of {clients.length}
                    </span>
                    <span className="flex items-center gap-2">
                      <Button size="sm" variant="ghost" disabled={activeClientPage === 0} onClick={() => setClientPage((page) => Math.max(0, page - 1))}>
                        Previous
                      </Button>
                      <span aria-live="polite">
                        Page {activeClientPage + 1} of {clientPageCount}
                      </span>
                      <Button size="sm" variant="ghost" disabled={activeClientPage >= clientPageCount - 1} onClick={() => setClientPage((page) => Math.min(clientPageCount - 1, page + 1))}>
                        Next
                      </Button>
                    </span>
                  </div>
                ) : null}
              </>
            )}
          </Panel>
        </div>

        <Panel aria-labelledby="mcp-calls">
          <PanelHead title={<span id="mcp-calls">Recent calls through MCP</span>} />
          {calls.length === 0 ? (
            <PanelBody>
              <p className="text-sm text-fg-muted">{canManage ? "Nothing yet." : "Admins see the calls made through MCP here."}</p>
            </PanelBody>
          ) : (
            <ul className="divide-y divide-border text-sm" aria-label="Recent MCP calls">
              {calls.map((e) => (
                <li key={e.id} className="flex flex-wrap items-center gap-2 px-3.5 py-2">
                  <span className="font-mono text-caption">{e.action}</span>
                  <span className="text-fg-muted">{e.actor_label ?? ""}</span>
                  <span className="flex-1" />
                  <Time value={e.created_at} tz={workspace.timezone} format="datetime" />
                </li>
              ))}
            </ul>
          )}
        </Panel>
        {children}
      </main>
    </>
  );
}
