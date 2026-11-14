"use client";

import { sandboxCaptures, sandboxSend, sandboxTap } from "@mera/sdk";
import { useCallback, useEffect, useId, useState } from "react";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * Sandbox inspector (M9.17). With no bot token, cards go to a sandbox phone: this page
 * shows what that phone received and lets you tap its buttons and type replies, through
 * the real bot runtime (identity checks, tokens and all). The right column shows every
 * simulated platform call, so a demo shows exactly what each account would have posted.
 */

type Btn = { label: string; data: string | null; url: string | null };
type Msg = { id: string | number; message_id: string | null; text: string; buttons: Btn[][]; at: string | null; edited?: boolean };
type Capture = { id: number; kind: string; target: string; payload: Record<string, unknown>; created_at: string; mine: boolean };

/** Telegram HTML to plain text for display (cards use <b>, <i>, <code>). */
export function plain(html: string): string {
  return html.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

export function SandboxScreen({ workspace }: { workspace: { id: string; slug: string; timezone: string } }) {
  const id = useId();
  const [phone, setPhone] = useState<Msg[]>([]);
  const [calls, setCalls] = useState<Capture[]>([]);
  const [text, setText] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const r = await settle(sandboxCaptures({ client: browserApi, path: { workspace_id: workspace.id } }));
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    const rows = (r.data.data as unknown as Capture[]).slice().reverse();
    setCalls(rows.filter((c) => c.kind === "platform_call").reverse());
    // The latest version of each message (cards are edited in place).
    const byMessage = new Map<string, Msg>();
    for (const c of rows.filter((x) => x.kind === "channel_message" && x.mine)) {
      const mid = String(c.payload.message_id ?? c.id);
      byMessage.set(mid, {
        id: c.id,
        message_id: c.payload.message_id ? String(c.payload.message_id) : null,
        text: String(c.payload.text ?? ""),
        buttons: (c.payload.buttons as Btn[][]) ?? [],
        at: c.created_at,
        edited: c.payload.op === "edit",
      });
    }
    setPhone((prev) => {
      // Immediate replies, minus any the capture log now carries (cards are logged there).
      const replies = prev.filter(
        (m) => typeof m.id === "string" && m.id.startsWith("r") && !(m.message_id && byMessage.has(m.message_id)),
      );
      return [...byMessage.values(), ...replies].sort((a, b) => String(a.at).localeCompare(String(b.at)));
    });
  }, [workspace.id]);

  useEffect(() => {
    const first = setTimeout(() => void load(), 0);
    const t = setInterval(() => void load(), 3000);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [load]);

  function addReplies(replies: { message_id: string; text: string; buttons: Btn[][] }[]) {
    const now = new Date().toISOString();
    setPhone((p) => [
      ...p,
      ...replies.map((r, i) => ({ id: `r${Date.now()}-${i}`, message_id: r.message_id, text: r.text, buttons: r.buttons, at: now })),
    ]);
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setError(null);
    const body = text;
    setText("");
    setPhone((p) => [...p, { id: `r${Date.now()}-me`, message_id: null, text: `› ${body}`, buttons: [], at: new Date().toISOString() }]);
    const r = await settle(sandboxSend({ client: browserApi, path: { workspace_id: workspace.id }, body: { text: body } }));
    if (!r.ok) return setError(displayMessage(r.error));
    addReplies(r.data.replies as never);
    void load();
  }

  async function tap(m: Msg, b: Btn) {
    if (b.url) return window.open(b.url, "_blank", "noreferrer");
    if (!b.data || !m.message_id) return;
    setError(null);
    const r = await settle(
      sandboxTap({ client: browserApi, path: { workspace_id: workspace.id }, body: { message_id: m.message_id, data: b.data } }),
    );
    if (!r.ok) return setError(displayMessage(r.error));
    setToast(r.data.answer ?? null);
    addReplies(r.data.replies as never);
    void load();
  }

  return (
    <>
      <Topbar title="Sandbox inspector" sub="Simulated Telegram and platforms" />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4 lg:grid-cols-2">
        <Panel>
          <PanelHead title="Your sandbox phone" actions={<Tag tone="info">Telegram, simulated</Tag>} />
          <PanelBody className="grid gap-3">
            <Notice tone="info">
              Pair first: get a code on Approval Channels, then send <code className="font-mono">/pair CODE</code> here.
            </Notice>
            {error ? <Notice tone="danger" role="alert">{error}</Notice> : null}
            <div role="status" aria-live="polite" className="empty:hidden">
              {toast ? <Notice tone="info">{toast}</Notice> : null}
            </div>
            <ol className="grid max-h-[60vh] gap-2 overflow-y-auto" aria-label="Messages">
              {phone.map((m) => (
                <li key={String(m.id)} className="grid gap-1.5 rounded-md border border-border bg-raised p-2.5">
                  <pre className="font-sans text-sm whitespace-pre-wrap">{plain(m.text)}</pre>
                  {m.buttons.map((row, i) => (
                    <div key={i} className="flex flex-wrap gap-1.5">
                      {row.map((b) => (
                        <Button key={`${b.label}-${b.data}`} size="sm" variant={b.label.startsWith("✅") ? "primary" : "default"} onClick={() => void tap(m, b)}>
                          {b.label}
                        </Button>
                      ))}
                    </div>
                  ))}
                  {m.at ? (
                    <span className="text-caption text-fg-subtle">
                      <Time value={m.at} tz={workspace.timezone} format="time" />
                      {m.edited ? " · edited" : ""}
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
            <form className="flex items-end gap-2" onSubmit={(e) => void send(e)}>
              <div className="grid flex-1 gap-1">
                <Label htmlFor={`${id}-t`}>Message</Label>
                <Input id={`${id}-t`} value={text} onChange={(e) => setText(e.target.value)} placeholder="/queue, /pair CODE, or an edit reply" />
              </div>
              <Button type="submit" variant="primary" size="sm">Send</Button>
            </form>
          </PanelBody>
        </Panel>
        <Panel>
          <PanelHead title="Platform calls" />
          <ol className="divide-y divide-border">
            {calls.length === 0 ? <li className="px-3.5 py-3 text-sm text-fg-subtle">No publishes yet.</li> : null}
            {calls.map((c) => {
              const p = c.payload as Record<string, unknown>;
              return (
                <li key={c.id} className="grid gap-1 px-3.5 py-2.5 text-sm">
                  <span className="flex flex-wrap items-center gap-2">
                    <Tag tone={p.outcome === "published" ? "ok" : p.outcome === "error" ? "danger" : "warn"}>{String(p.outcome)}</Tag>
                    <span className="font-medium">{c.target}</span>
                    <span className="text-caption text-fg-subtle">attempt {String(p.attempt)}</span>
                    <span className="flex-1" />
                    <Time value={c.created_at} tz={workspace.timezone} format="time" className="text-caption text-fg-subtle" />
                  </span>
                  <span className="line-clamp-3 text-fg-muted">{String(p.text ?? "")}</span>
                  {p.error ? <span className="text-caption text-danger">{String(p.error)}</span> : null}
                  {p.permalink ? <span className="font-mono text-xs text-fg-subtle">{String(p.permalink)}</span> : null}
                </li>
              );
            })}
          </ol>
        </Panel>
      </main>
    </>
  );
}
