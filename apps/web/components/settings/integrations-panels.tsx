"use client";

import {
  createWebhook,
  deleteMe,
  deleteWebhook,
  exportLink,
  exportMe,
  listExports,
  listWebhooks,
  requestExport,
  securityAlerts,
  setDigest,
  testWebhook,
  updateWebhook,
} from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { StepUpPrompt, useStepUpProof } from "@/components/mera/step-up";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { ALERT_COPY, DIGEST_MODES, WEBHOOK_EVENTS, formatBytes, isHHMM } from "@/lib/integrations";

/*
 * Settings added in Phase 3: your approval digest (P3-M4.1), outbound webhooks (P3-M4.3),
 * workspace exports and security alerts (P3-M7, Admins), and your own data: a copy of it,
 * or deleting your account (P3-M7.2).
 */

type Hook = {
  id: string;
  url: string;
  description: string | null;
  events: string[];
  active: boolean;
  consecutive_failures: number;
  disabled_reason: string | null;
  secret?: string | null;
};

export function DigestPanel({ workspaceId, timezone }: { workspaceId: string; timezone: string }) {
  const [mode, setMode] = useState("immediate");
  const [time, setTime] = useState("08:30");
  const [msg, setMsg] = useState<{ tone: "info" | "danger"; text: string } | null>(null);

  async function save() {
    if (!isHHMM(time)) return setMsg({ tone: "danger", text: "Use a time like 08:30." });
    const r = await settle(setDigest({ client: browserApi, path: { workspace_id: workspaceId }, body: { mode, time } }));
    setMsg(r.ok ? { tone: "info", text: "Saved." } : { tone: "danger", text: displayMessage(r.error) });
  }

  return (
    <Panel>
      <PanelHead title="Approval notifications" />
      <PanelBody className="grid gap-3">
        <Field label="How you hear about posts waiting for you">
          <Select value={mode} onChange={(e) => setMode(e.target.value)}>
            {DIGEST_MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </Select>
        </Field>
        {mode !== "immediate" ? (
          <Field label={`Daily digest at (${timezone})`}>
            <Input className="w-28" value={time} onChange={(e) => setTime(e.target.value)} />
          </Field>
        ) : null}
        {msg ? <Notice tone={msg.tone}>{msg.text}</Notice> : null}
        <div>
          <Button size="sm" variant="primary" onClick={() => void save()}>
            Save
          </Button>
        </div>
      </PanelBody>
    </Panel>
  );
}

export function WebhooksPanel({ workspaceId, archived }: { workspaceId: string; archived: boolean }) {
  const path = { workspace_id: workspaceId };
  const [hooks, setHooks] = useState<Hook[] | null>(null);
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["post.published", "post.failed"]);
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await settle(listWebhooks({ client: browserApi, path }));
    if (r.ok) setHooks((r.data as { data: Hook[] }).data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads after mount; state is set after the await
    void load();
  }, [load]);

  async function add() {
    setError(null);
    const r = await settle(createWebhook({ client: browserApi, path, body: { url: url.trim(), events } }));
    if (!r.ok) return setError(displayMessage(r.error));
    setSecret((r.data as Hook).secret ?? null);
    setUrl("");
    await load();
  }

  async function act(id: string, what: "test" | "rotate" | "toggle" | "delete", active?: boolean) {
    setError(null);
    const p = { ...path, webhook_id: id };
    const r =
      what === "test"
        ? await settle(testWebhook({ client: browserApi, path: p }))
        : what === "delete"
          ? await settle(deleteWebhook({ client: browserApi, path: p }))
          : await settle(
              updateWebhook({
                client: browserApi,
                path: p,
                body: what === "rotate" ? { rotate_secret: true } : { active: !active },
              }),
            );
    if (!r.ok) return setError(displayMessage(r.error));
    if (what === "rotate") setSecret((r.data as Hook).secret ?? null);
    await load();
  }

  return (
    <Panel>
      <PanelHead title="Webhooks" />
      <PanelBody className="grid gap-3">
        <p className="text-sm text-fg-muted">
          Mera POSTs each event as JSON, signed in the <code>Mera-Signature</code> header (<code>t=…,v1=…</code>, HMAC-SHA256
          of <code>t.body</code> with your secret). Failed deliveries are retried with back-off; 20 failures in a row turn
          the endpoint off.
        </p>
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        {secret ? (
          <Notice tone="warn" title="Signing secret, shown once">
            <code className="break-all">{secret}</code>
          </Notice>
        ) : null}
        {hooks && hooks.length > 0 ? (
          <ul className="divide-y divide-border">
            {hooks.map((h) => (
              <li key={h.id} className="grid gap-1 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="break-all font-mono text-mono-xs">{h.url}</span>
                  {h.active ? <Tag tone="ok">On</Tag> : <Tag tone="warn">Off</Tag>}
                  {h.consecutive_failures > 0 ? <Tag tone="danger">{h.consecutive_failures} failing</Tag> : null}
                </div>
                <p className="text-caption text-fg-muted">{h.events.join(", ")}</p>
                {h.disabled_reason ? <p className="text-caption text-danger">{h.disabled_reason}</p> : null}
                {!archived ? (
                  <div className="flex flex-wrap gap-1.5">
                    <Button size="sm" variant="ghost" onClick={() => void act(h.id, "test")}>
                      Send a test
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(h.id, "toggle", h.active)}>
                      {h.active ? "Turn off" : "Turn on"}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(h.id, "rotate")}>
                      New secret
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(h.id, "delete")}>
                      Delete
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        ) : hooks ? (
          <p className="text-sm text-fg-muted">No webhooks yet.</p>
        ) : null}
        {!archived ? (
          <div className="grid gap-2">
            <Field label="Endpoint URL" hint="https only.">
              <Input value={url} placeholder="https://example.com/hooks/mera" onChange={(e) => setUrl(e.target.value)} />
            </Field>
            <fieldset className="grid gap-1">
              <legend className="text-sm font-semibold">Events</legend>
              <div className="grid grid-cols-2 gap-1 max-rail:grid-cols-1">
                {WEBHOOK_EVENTS.map((ev) => (
                  <label key={ev} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={events.includes(ev)}
                      onChange={(e) => setEvents((xs) => (e.target.checked ? [...xs, ev] : xs.filter((x) => x !== ev)))}
                    />
                    <span className="font-mono text-mono-xs">{ev}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div>
              <Button size="sm" variant="primary" disabled={!url.trim() || events.length === 0} onClick={() => void add()}>
                Add webhook
              </Button>
            </div>
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

type ExportRow = { id: string; state: string; byte_size: number | null; error: string | null; created_at: string; expires_at: string | null };

export function ExportsPanel({ workspaceId, timezone }: { workspaceId: string; timezone: string }) {
  const path = { workspace_id: workspaceId };
  const [rows, setRows] = useState<ExportRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await settle(listExports({ client: browserApi, path }));
    if (r.ok) setRows((r.data as unknown as { data: ExportRow[] }).data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads after mount; state is set after the await
    void load();
  }, [load]);

  async function start() {
    setError(null);
    const r = await settle(requestExport({ client: browserApi, path }));
    if (!r.ok) return setError(displayMessage(r.error));
    await load();
  }

  async function download(id: string) {
    const r = await settle(exportLink({ client: browserApi, path: { ...path, export_id: id } }));
    if (!r.ok) return setError(displayMessage(r.error));
    window.location.assign((r.data as { url: string }).url);
  }

  return (
    <Panel>
      <PanelHead
        title="Workspace export"
        actions={
          <Button size="sm" onClick={() => void start()}>
            Export everything
          </Button>
        }
      />
      <PanelBody className="grid gap-2">
        <p className="text-sm text-fg-muted">
          A zip of the workspace&rsquo;s data as JSON. Credentials and secrets are never included. Links last 15 minutes.
        </p>
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        {rows && rows.length > 0 ? (
          <ul className="divide-y divide-border text-sm">
            {rows.map((x) => (
              <li key={x.id} className="flex flex-wrap items-center gap-2 py-1.5">
                <Time value={x.created_at} tz={timezone} />
                <Tag tone={x.state === "ready" ? "ok" : x.state === "failed" ? "danger" : "neutral"}>{x.state}</Tag>
                {x.byte_size ? <span className="tabular text-fg-muted">{formatBytes(x.byte_size)}</span> : null}
                {x.state === "ready" ? (
                  <Button size="sm" variant="ghost" onClick={() => void download(x.id)}>
                    Download
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

type Alert = { id: string; kind: string; created_at: string; recipients: unknown };

export function SecurityAlertsPanel({ workspaceId, timezone }: { workspaceId: string; timezone: string }) {
  const [rows, setRows] = useState<Alert[] | null>(null);
  useEffect(() => {
    void settle(securityAlerts({ client: browserApi, path: { workspace_id: workspaceId } })).then((r) => {
      if (r.ok) setRows((r.data as { data: Alert[] }).data);
    });
  }, [workspaceId]);
  return (
    <Panel>
      <PanelHead title="Security alerts" />
      <PanelBody className="grid gap-2">
        <p className="text-sm text-fg-muted">
          Lockouts, a reused sign-in token, bursts of failed confirmations, and autopilot changes. Owners and Admins are
          emailed within a minute.
        </p>
        {rows && rows.length > 0 ? (
          <ul className="divide-y divide-border text-sm">
            {rows.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2 py-1.5">
                <Time value={a.created_at} tz={timezone} />
                <span>{ALERT_COPY[a.kind] ?? a.kind}</span>
              </li>
            ))}
          </ul>
        ) : rows ? (
          <p className="text-sm text-fg-muted">No alerts.</p>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

export function MyDataPanel({ passwordSet, mfaEnabled }: { passwordSet: boolean; mfaEnabled: boolean }) {
  const router = useRouter();
  const [proof, setProof] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stepUp, clearStepUp] = useStepUpProof();
  const providerOnly = !passwordSet && !mfaEnabled;
  const value = providerOnly ? (stepUp.proof ?? "") : proof;

  async function download() {
    const r = await settle(exportMe({ client: browserApi }));
    if (!r.ok) return setError(displayMessage(r.error));
    const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "mera-my-data.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function remove() {
    setError(null);
    const r = await settle(deleteMe({ client: browserApi, body: { proof: value } }));
    clearStepUp();
    setProof("");
    if (!r.ok) return setError(displayMessage(r.error));
    router.push("/login");
  }

  return (
    <Panel tone="danger">
      <PanelHead title="Your data" />
      <PanelBody className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => void download()}>
            Download my data
          </Button>
          <span className="text-sm text-fg-muted">Everything Mera holds about you, as JSON.</span>
        </div>
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        {!confirming ? (
          <div>
            <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
              Delete my account…
            </Button>
          </div>
        ) : (
          <div className="grid gap-2">
            <Notice tone="warn">
              You are signed out everywhere now; your personal data is erased after 30 days. If you are the only Owner of a
              workspace, hand it over or delete it first.
            </Notice>
            {providerOnly ? (
              <StepUpPrompt state={stepUp} purpose="delete your account" />
            ) : (
              <Field label={mfaEnabled ? "Current authenticator code" : "Your password"}>
                <Input
                  value={proof}
                  type={mfaEnabled ? "text" : "password"}
                  autoComplete={mfaEnabled ? "one-time-code" : "current-password"}
                  onChange={(e) => setProof(e.target.value)}
                />
              </Field>
            )}
            <div className="flex gap-2">
              <Button size="sm" variant="danger" disabled={!value} onClick={() => void remove()}>
                Delete my account
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </PanelBody>
      <PanelFoot>Workspace content you created stays with the workspace; your name on it is replaced.</PanelFoot>
    </Panel>
  );
}
