import { clientInfo } from "@mera/sdk";
import type { Metadata } from "next";
import { ConsentForm } from "@/components/mcp/consent-form";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody } from "@/components/ui/panel";
import { getSession, serverClient } from "@/lib/api/server";

export const metadata: Metadata = { title: "Connect an MCP client" };

const KEYS = ["client_id", "redirect_uri", "code_challenge", "code_challenge_method", "scope", "resource", "state", "response_type"] as const;

/*
 * OAuth 2.1 consent for MCP clients (P2-M8.2, docs/05 §1.2). The client is identified
 * by its Client ID Metadata Document, read by the API; this page shows who is asking,
 * for which workspace and which scopes, and sends the person back with a code.
 */
export default async function AuthorizePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const q: Record<string, string> = {};
  for (const k of KEYS) {
    const v = sp[k];
    if (typeof v === "string") q[k] = v.slice(0, 2048);
  }
  const here = `/oauth/authorize?${new URLSearchParams(q).toString()}`;
  const session = await getSession(here);
  const problem =
    q.response_type !== "code"
      ? "This link is not a valid authorization request (response_type must be code)."
      : !q.client_id || !q.redirect_uri || !q.code_challenge || !q.resource
        ? "This authorization request is missing required parameters."
        : (q.code_challenge_method ?? "S256") !== "S256"
          ? "This client must use PKCE with S256."
          : null;
  let client: { client_id: string; client_name: string; redirect_uris: string[]; logo_uri: string | null } | null = null;
  let clientError: string | null = null;
  if (!problem) {
    const r = await clientInfo({ client: await serverClient(), query: { client_id: q.client_id! } });
    if (r.data) client = r.data;
    else clientError = "Mera could not read this client's metadata document, so it cannot be trusted.";
  }
  if (problem || clientError || !client) {
    return (
      <Panel aria-labelledby="consent-title">
        <PanelBody className="grid gap-3 p-6">
          <h1 id="consent-title" className="text-lg">
            Connect an MCP client
          </h1>
          <Notice tone="danger" role="alert">
            {problem ?? clientError}
          </Notice>
        </PanelBody>
      </Panel>
    );
  }
  return (
    <ConsentForm
      client={client}
      request={{
        client_id: q.client_id!,
        redirect_uri: q.redirect_uri!,
        code_challenge: q.code_challenge!,
        scope: q.scope ?? "drafts:read",
        resource: q.resource!,
        state: q.state ?? null,
      }}
      workspaces={session.workspaces.map((w) => ({ id: w.id, name: w.name }))}
      issuer={process.env.MERA_PUBLIC_API_URL ?? "http://localhost:8000"}
    />
  );
}
