"use client";

import {
  approveServer,
  decideCall,
  disableServer,
  discoverServer,
  importServers,
  registerServer,
  rejectServer,
  setBindings,
  startServerOauth,
  updateTool,
} from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { useSecondFactor } from "@/components/mera/second-factor";
import { CatalogPanel } from "./catalog-panel";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * External MCP servers (P2-M9, docs/05 §2). A registration is unusable until an Admin
 * approves it with a second factor; every discovered tool starts disabled, and a tool an
 * uncurated server calls "read-only" is still treated as a write until someone says
 * otherwise. Write calls from agents wait here (and on the phone) with their exact
 * arguments. Disabling a server is instant.
 */

export interface RegistryTool {
  id: string;
  name: string;
  title: string | null;
  description: string;
  risk_class: "read" | "write" | "destructive";
  approval_policy: "always" | "auto" | "blocked";
  enabled: boolean;
  flag: string | null;
  flag_acknowledged: boolean;
  presence: string;
  agents: string[];
  exposed_name: string;
  max_calls_per_run?: number | null;
  max_calls_per_day?: number | null;
}

export interface RegistryServer {
  id: string;
  slug: string;
  name: string;
  transport: "http" | "stdio";
  url: string | null;
  package: string | null;
  state: string;
  protocol_era: string | null;
  auth_method: string;
  has_credentials: boolean;
  egress_allowlist: string[];
  last_discovered_at: string | null;
  last_error: string | null;
  degraded?: boolean;
  tools: RegistryTool[];
}

export interface RegistryCall {
  id: string;
  server_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  content_hash: string;
  agent_key: string | null;
  state: string;
  error: string | null;
  expires_at: string;
}

const STATE_TONE: Record<string, "ok" | "warn" | "danger" | "neutral" | "info"> = {
  active: "ok",
  pending_review: "info",
  degraded: "warn",
  auth_required: "warn",
  disabled: "danger",
  rejected: "neutral",
};

type Run = () => Promise<{ ok: true; data: unknown } | { ok: false; error: Parameters<typeof displayMessage>[0] }>;

export function RegistryPanel({
  workspace,
  servers,
  calls,
  canManage,
  canDecide,
}: {
  workspace: { id: string; timezone: string };
  servers: RegistryServer[];
  calls: RegistryCall[];
  canManage: boolean;
  canDecide: boolean;
}) {
  const id = useId();
  const router = useRouter();
  const { withSecondFactor } = useSecondFactor();
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [form, setForm] = useState({ slug: "", name: "", transport: "http", target: "", allowlist: "" });
  const [json, setJson] = useState("");
  const path = { workspace_id: workspace.id };

  async function act(key: string, run: Run, ok: string) {
    setBusy(key);
    setNote(null);
    const r = await run();
    setBusy(null);
    if (!r.ok) {
      setNote({ tone: "danger", text: displayMessage(r.error) });
      return false;
    }
    setNote({ tone: "info", text: ok });
    router.refresh();
    return true;
  }

  async function register() {
    const http = form.transport === "http";
    const body = {
      slug: form.slug.trim(),
      name: form.name.trim() || form.slug.trim(),
      transport: form.transport as "http" | "stdio",
      url: http ? form.target.trim() : null,
      package: http ? null : form.target.trim(),
      egress_allowlist: form.allowlist.split(/[\s,]+/).filter(Boolean),
    };
    const done = await act("register", () => settle(registerServer({ client: browserApi, path, body })), "Registered. Test the connection, then review its tools.");
    if (done) setForm({ slug: "", name: "", transport: "http", target: "", allowlist: "" });
  }

  async function importJson() {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(json) as Record<string, unknown>;
    } catch {
      setNote({ tone: "danger", text: "That is not valid JSON." });
      return;
    }
    if (await act("import", () => settle(importServers({ client: browserApi, path, body: { config } })), "Imported for review. Secrets were moved to encrypted storage.")) setJson("");
  }

  return (
    <>
      <Panel aria-labelledby={`${id}-servers`}>
        <PanelHead title={<span id={`${id}-servers`}>External MCP servers for agents</span>} />
        <PanelBody className="grid gap-3">
          <div role="status" aria-live="polite" className="empty:hidden">
            {note?.tone === "info" ? <Notice tone="info" icon="check">{note.text}</Notice> : null}
          </div>
          {note?.tone === "danger" ? (
            <Notice tone="danger" role="alert">
              {note.text}
            </Notice>
          ) : null}
          {servers.length === 0 ? <p className="text-sm text-fg-muted">No external servers. Agents use only Mera&apos;s own tools.</p> : null}
          <ul className="grid gap-3" aria-label="Registered servers">
            {servers.map((srv) => (
              <li key={srv.id} className="grid gap-2 rounded-md border border-border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{srv.name}</span>
                  <span className="font-mono text-caption text-fg-subtle">{srv.slug}</span>
                  <Tag tone={STATE_TONE[srv.state] ?? "neutral"}>{srv.state.replace("_", " ")}</Tag>
                  {srv.protocol_era ? <Tag tone="outline">{srv.protocol_era}</Tag> : null}
                  {srv.degraded ? <Tag tone="danger">failing, paused</Tag> : null}
                  <span className="flex-1" />
                  {canManage && srv.state !== "disabled" ? (
                    <>
                      <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void act(`d:${srv.id}`, () => settle(discoverServer({ client: browserApi, path: { ...path, server_id: srv.id } })), "Connection tested and tools refreshed.")}>
                        Test connection
                      </Button>
                      {srv.transport === "http" ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy !== null}
                          onClick={async () => {
                            const r = await settle(startServerOauth({ client: browserApi, path: { ...path, server_id: srv.id } }));
                            if (r.ok && r.data.authorize_url.startsWith("https://")) window.open(r.data.authorize_url, "_blank", "noopener");
                            else if (r.ok) setNote({ tone: "danger", text: "The server's sign-in address is not https; it was not opened." });
                            else setNote({ tone: "danger", text: displayMessage(r.error) });
                          }}
                        >
                          Authorize
                        </Button>
                      ) : null}
                      {srv.state === "pending_review" ? (
                        <>
                          <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => void act(`a:${srv.id}`, () => withSecondFactor((headers) => settle(approveServer({ client: browserApi, headers, path: { ...path, server_id: srv.id } }))), "Approved. Enable and bind the tools agents may use.")}>
                            Approve
                          </Button>
                          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void act(`r:${srv.id}`, () => settle(rejectServer({ client: browserApi, path: { ...path, server_id: srv.id } })), "Rejected.")}>
                            Reject
                          </Button>
                        </>
                      ) : null}
                      <Button size="sm" variant="danger" disabled={busy !== null} onClick={() => void act(`x:${srv.id}`, () => settle(disableServer({ client: browserApi, path: { ...path, server_id: srv.id } })), "Disabled. Pending calls were cancelled.")}>
                        Disable
                      </Button>
                    </>
                  ) : null}
                </div>
                <p className="font-mono text-caption text-fg-muted">{srv.url ?? srv.package}</p>
                {srv.last_error ? <Notice tone="warn">{srv.last_error}</Notice> : null}
                {srv.tools.length > 0 ? (
                  <table className="w-full text-sm" aria-label={`Tools of ${srv.name}`}>
                    <thead className="text-left text-caption text-fg-subtle">
                      <tr>
                        <th className="py-1 font-medium">Tool</th>
                        <th className="py-1 font-medium">Risk</th>
                        <th className="py-1 font-medium">Policy</th>
                        <th className="py-1 font-medium">Agents</th>
                        <th className="py-1 font-medium">On</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {srv.tools.map((t) => (
                        <ToolRow key={t.id} tool={t} canManage={canManage} busy={busy !== null} workspaceId={workspace.id} act={act} withSecondFactor={withSecondFactor} />
                      ))}
                    </tbody>
                  </table>
                ) : null}
              </li>
            ))}
          </ul>
        </PanelBody>
      </Panel>

      {calls.length > 0 ? (
        <Panel aria-labelledby={`${id}-calls`}>
          <PanelHead title={<span id={`${id}-calls`}>Tool calls waiting for a person</span>} />
          <ul className="divide-y divide-border" aria-label="Tool calls waiting">
            {calls.map((c) => (
              <li key={c.id} className="grid gap-2 px-3.5 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono">{c.tool_name}</span>
                  <span className="text-fg-muted">asked by the {c.agent_key ?? "unknown"} agent</span>
                  <span className="flex-1" />
                  <span className="text-caption text-fg-subtle">
                    expires <Time value={c.expires_at} tz={workspace.timezone} format="time" />
                  </span>
                </div>
                <pre className="overflow-x-auto rounded-md bg-surface-sunken p-2.5 font-mono text-mono-xs" aria-label="Exact arguments">
                  {JSON.stringify(c.arguments, null, 2)}
                </pre>
                {canDecide ? (
                  <div className="flex gap-2">
                    <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => void act(`c:${c.id}`, () => settle(decideCall({ client: browserApi, path: { ...path, call_id: c.id }, body: { allow: true, content_hash: c.content_hash } })), "Allowed. It runs with exactly these arguments.")}>
                      Allow
                    </Button>
                    <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void act(`c:${c.id}`, () => settle(decideCall({ client: browserApi, path: { ...path, call_id: c.id }, body: { allow: false, content_hash: c.content_hash } })), "Denied. The agent continues without it.")}>
                      Deny
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {canManage ? <CatalogPanel workspaceId={workspace.id} /> : null}

      {canManage ? (
        <Panel aria-labelledby={`${id}-add`}>
          <PanelHead title={<span id={`${id}-add`}>Add a server</span>} />
          <PanelBody className="grid gap-4">
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="grid gap-1">
                <Label htmlFor={`${id}-slug`}>Slug</Label>
                <Input id={`${id}-slug`} value={form.slug} placeholder="notion" onChange={(e) => setForm({ ...form, slug: e.target.value })} />
              </div>
              <div className="grid gap-1">
                <Label htmlFor={`${id}-name`}>Name</Label>
                <Input id={`${id}-name`} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="grid gap-1">
                <Label htmlFor={`${id}-transport`}>Transport</Label>
                <Select id={`${id}-transport`} value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}>
                  <option value="http">Remote (https URL)</option>
                  <option value="stdio">Package, run in the sandbox</option>
                </Select>
              </div>
              <div className="grid gap-1">
                <Label htmlFor={`${id}-target`}>{form.transport === "http" ? "URL" : "Pinned package"}</Label>
                <Input
                  id={`${id}-target`}
                  value={form.target}
                  placeholder={form.transport === "http" ? "https://mcp.example.com/mcp" : "npm:@scope/server@1.2.3"}
                  onChange={(e) => setForm({ ...form, target: e.target.value })}
                />
              </div>
              <div className="grid gap-1 sm:col-span-2">
                <Label htmlFor={`${id}-allow`}>Hosts it may reach (sandboxed servers)</Label>
                <Input id={`${id}-allow`} value={form.allowlist} placeholder="api.notion.com" onChange={(e) => setForm({ ...form, allowlist: e.target.value })} />
              </div>
            </div>
            <div>
              <Button size="sm" variant="primary" disabled={busy !== null || !form.slug.trim() || !form.target.trim()} onClick={() => void register()}>
                Register for review
              </Button>
            </div>
            <div className="grid gap-1">
              <Label htmlFor={`${id}-json`}>Or paste an mcpServers object (Claude Desktop format)</Label>
              <Textarea id={`${id}-json`} rows={4} className="font-mono" value={json} onChange={(e) => setJson(e.target.value)} />
              <p className="text-caption text-fg-subtle">Versions must be pinned. Shell commands are refused. Values in env and headers become encrypted credentials.</p>
              <div>
                <Button size="sm" disabled={busy !== null || !json.trim()} onClick={() => void importJson()}>
                  Import for review
                </Button>
              </div>
            </div>
          </PanelBody>
        </Panel>
      ) : null}
    </>
  );
}

function ToolRow({
  tool: t,
  canManage,
  busy,
  workspaceId,
  act,
  withSecondFactor,
}: {
  tool: RegistryTool;
  canManage: boolean;
  busy: boolean;
  workspaceId: string;
  act: (key: string, run: Run, ok: string) => Promise<boolean>;
  withSecondFactor: ReturnType<typeof useSecondFactor>["withSecondFactor"];
}) {
  const [agents, setAgents] = useState(t.agents.join(", "));
  const [perDay, setPerDay] = useState(t.max_calls_per_day ? String(t.max_calls_per_day) : "");
  const saveBindings = () => {
    const day = perDay.trim() ? Number(perDay) : null;
    if (day !== null && !(Number.isInteger(day) && day >= 1 && day <= 10000)) return;
    void act(
      `b:${t.id}`,
      () =>
        settle(
          setBindings({
            client: browserApi,
            path,
            body: {
              agent_keys: agents.split(/[\s,]+/).filter(Boolean),
              max_calls_per_run: t.max_calls_per_run ?? null,
              max_calls_per_day: day,
            },
          }),
        ),
      "Agents saved.",
    );
  };
  const path = { workspace_id: workspaceId, tool_id: t.id };
  const patch = (body: Parameters<typeof updateTool>[0]["body"], ok: string) =>
    act(`t:${t.id}`, () => withSecondFactor((headers) => settle(updateTool({ client: browserApi, headers, path, body }))), ok);
  return (
    <tr className="align-top">
      <td className="py-1.5 pr-2">
        <span className="font-mono">{t.name}</span>
        {t.presence !== "present" ? <Tag tone="warn" className="ml-1">{t.presence}</Tag> : null}
        <p className="text-caption text-fg-subtle">{t.description}</p>
        {t.flag && !t.flag_acknowledged ? (
          <Notice tone="warn" className="mt-1">
            {t.flag}{" "}
            {canManage ? (
              <button type="button" className="font-semibold underline" onClick={() => void patch({ acknowledge_flag: true }, "Warning acknowledged.")}>
                I have read it
              </button>
            ) : null}
          </Notice>
        ) : null}
      </td>
      <td className="py-1.5 pr-2">
        <Select aria-label={`Risk of ${t.name}`} value={t.risk_class} disabled={!canManage || busy} onChange={(e) => void patch({ risk_class: e.target.value as RegistryTool["risk_class"] }, "Risk class saved.")}>
          <option value="read">read</option>
          <option value="write">write</option>
          <option value="destructive">destructive</option>
        </Select>
      </td>
      <td className="py-1.5 pr-2">
        <Select aria-label={`Policy of ${t.name}`} value={t.approval_policy} disabled={!canManage || busy} onChange={(e) => void patch({ approval_policy: e.target.value as RegistryTool["approval_policy"] }, "Policy saved.")}>
          <option value="always">ask every time</option>
          <option value="auto">no approval</option>
          <option value="blocked">blocked</option>
        </Select>
      </td>
      <td className="py-1.5 pr-2">
        <Input
          aria-label={`Agents that may call ${t.name}`}
          value={agents}
          disabled={!canManage || busy}
          placeholder="writer, research"
          onChange={(e) => setAgents(e.target.value)}
          onBlur={() => {
            if (agents === t.agents.join(", ")) return;
            saveBindings();
          }}
        />
        <Input
          aria-label={`Calls per day to ${t.name}`}
          className="mt-1 w-28"
          inputMode="numeric"
          value={perDay}
          disabled={!canManage || busy}
          placeholder="200 a day"
          onChange={(e) => setPerDay(e.target.value)}
          onBlur={() => {
            if (perDay === (t.max_calls_per_day ? String(t.max_calls_per_day) : "")) return;
            saveBindings();
          }}
        />
      </td>
      <td className="py-1.5">
        <input
          type="checkbox"
          aria-label={`Enable ${t.name}`}
          checked={t.enabled}
          disabled={!canManage || busy}
          onChange={(e) => void patch({ enabled: e.target.checked }, e.target.checked ? "Enabled." : "Disabled.")}
        />
      </td>
    </tr>
  );
}
