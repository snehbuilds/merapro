"use client";

import { authorize } from "@mera/sdk";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot } from "@/components/ui/panel";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/* What each scope lets a client do, in plain words. */
export const SCOPE_TEXT: Readonly<Record<string, string>> = {
  "drafts:read": "Read your post sets and drafts",
  "drafts:write": "Create drafts and send them for approval (never approve or publish)",
  "groups:read": "See your account groups",
  "groups:write": "Change account groups",
  "accounts:read": "See your connected accounts",
  "approvals:read": "See what is waiting for approval",
  "approvals:decide": "Approve or reject posts as you",
  "publishing:read": "Read the calendar",
  "publishing:write": "Schedule posts",
  "engagement:read": "Read comments on your posts",
  "engagement:write": "Draft replies (sent only after approval)",
  "analytics:read": "Read analytics",
  "automation:write": "Stop automation (resuming stays with you)",
  "agents:read": "See agent runs and costs",
  "agents:write": "Change agent settings",
  "sources:read": "Read your sources",
  "sources:write": "Add and change sources",
  "content:read": "Read discovered content",
  "media:write": "Upload and generate media",
};

export interface ConsentFormProps {
  client: { client_id: string; client_name: string; redirect_uris: string[] };
  request: { client_id: string; redirect_uri: string; code_challenge: string; scope: string; resource: string; state: string | null };
  workspaces: { id: string; name: string }[];
  issuer: string;
}

function loopbackMatch(asked: string, listed: string): boolean {
  try {
    const a = new URL(asked);
    const b = new URL(listed);
    const loop = (h: string) => h === "localhost" || h === "127.0.0.1" || h === "[::1]";
    return a.protocol === "http:" && b.protocol === "http:" && loop(a.hostname) && a.hostname === b.hostname && a.pathname === b.pathname;
  } catch {
    return false;
  }
}

/**
 * Where this page may send the browser: https, http to the loopback interface, or a
 * reverse-DNS app scheme (RFC 8252). Never javascript:, data: and the like, which would
 * run in Mera's origin. The API applies the same rule; this is the last line before
 * window.location.
 */
export function safeRedirect(uri: string): boolean {
  let u: URL;
  try {
    u = new URL(uri);
  } catch {
    return false;
  }
  const scheme = u.protocol.slice(0, -1).toLowerCase();
  if (u.hash || /[\s\\]/.test(uri)) return false;
  if (scheme === "https") return Boolean(u.hostname);
  if (scheme === "http") return ["localhost", "127.0.0.1", "[::1]"].includes(u.hostname);
  const never = ["javascript", "data", "vbscript", "file", "blob", "about", "filesystem"];
  return /^[a-z][a-z0-9+.-]*$/.test(scheme) && scheme.includes(".") && !never.includes(scheme);
}

export function ConsentForm({ client, request, workspaces, issuer }: ConsentFormProps) {
  const id = useId();
  const [workspace, setWorkspace] = useState(workspaces[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scopes = request.scope.split(/\s+/).filter(Boolean);
  const redirectKnown =
    safeRedirect(request.redirect_uri) &&
    client.redirect_uris.some((u) => u === request.redirect_uri || loopbackMatch(request.redirect_uri, u));
  const host = (() => {
    try {
      return new URL(client.client_id).host;
    } catch {
      return client.client_id;
    }
  })();

  async function allow() {
    setBusy(true);
    setError(null);
    const r = await settle(authorize({ client: browserApi, body: { ...request, workspace_id: workspace, code_challenge_method: "S256" } }));
    if (!r.ok) {
      setBusy(false);
      setError(displayMessage(r.error));
      return;
    }
    if (!safeRedirect(r.data.redirect_to)) {
      setBusy(false);
      setError("This client's return address is not safe to open.");
      return;
    }
    window.location.assign(r.data.redirect_to);
  }

  function deny() {
    if (!redirectKnown) return;
    const u = new URL(request.redirect_uri);
    u.searchParams.set("error", "access_denied");
    u.searchParams.set("iss", issuer);
    if (request.state) u.searchParams.set("state", request.state);
    const target = u.toString();
    if (safeRedirect(target)) window.location.assign(target);
  }

  return (
    <Panel aria-labelledby={`${id}-title`}>
      <PanelBody className="grid gap-4 p-6 max-narrow:p-4">
        <div className="grid gap-1">
          <h1 id={`${id}-title`} className="text-lg">
            Connect {client.client_name}
          </h1>
          <p className="text-sm text-fg-muted">
            <span className="font-mono">{host}</span> wants to use Mera on your behalf.
          </p>
        </div>
        {!redirectKnown ? (
          <Notice tone="danger" role="alert">
            This client did not register the address it wants to send you back to. Do not continue.
          </Notice>
        ) : null}
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        <div className="grid gap-1">
          <Label htmlFor={`${id}-ws`}>Workspace</Label>
          <Select id={`${id}-ws`} value={workspace} onChange={(e) => setWorkspace(e.target.value)} disabled={busy}>
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1.5">
          <h2 className="text-sm font-semibold">It will be able to</h2>
          <ul className="grid gap-1 text-sm">
            {scopes.map((s) => (
              <li key={s} className="flex gap-2">
                <span aria-hidden="true">·</span>
                <span>
                  {SCOPE_TEXT[s] ?? s} <span className="font-mono text-caption text-fg-subtle">{s}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="text-caption text-fg-subtle">
            It can never connect accounts, change members, resume automation or see credentials. Posts still need an approval.
          </p>
        </div>
      </PanelBody>
      <PanelFoot className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={busy || !workspace || !redirectKnown} onClick={() => void allow()}>
          {busy ? "Connecting…" : "Allow"}
        </Button>
        <Button variant="ghost" disabled={busy || !redirectKnown} onClick={deny}>
          Deny
        </Button>
      </PanelFoot>
    </Panel>
  );
}
