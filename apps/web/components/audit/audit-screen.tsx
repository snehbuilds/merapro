"use client";

import { auditLog, type AuditEntryOut } from "@mera/sdk";
import Link from "next/link";
import { useState } from "react";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { AUDIT_FILTERS } from "@/lib/audit";
import { SCREENS } from "@/lib/nav";

/*
 * Audit Log (audit.html, M11): who did what, when, through which surface, on which
 * account, against which exact text. The actor's *current* name is shown, with the name
 * recorded at the time beside it when they differ, so a renamed user cannot pass as
 * someone else (Sprint 2 carry-over).
 */


export interface AuditScreenProps {
  workspace: { id: string; slug: string; timezone: string };
  entries: AuditEntryOut[];
  action: string;
  accounts: { id: string; nickname: string }[];
  accountId: string | null;
}

export function AuditScreen({ workspace, entries: first, action, accounts, accountId }: AuditScreenProps) {
  const [entries, setEntries] = useState(first);
  const [more, setMore] = useState(first.length === 50);
  const [error, setError] = useState<string | null>(null);
  const names = new Map(accounts.map((a) => [a.id, a.nickname]));
  const link = (a: string, acc: string | null = accountId) => {
    const q = new URLSearchParams();
    if (a) q.set("action", a);
    if (acc) q.set("account", acc);
    const s = q.toString();
    return `/w/${workspace.slug}/audit${s ? `?${s}` : ""}`;
  };

  async function loadMore() {
    const last = entries[entries.length - 1];
    const r = await settle(
      auditLog({
        client: browserApi,
        path: { workspace_id: workspace.id },
        query: { before_id: last?.id, limit: 50, action: action || null, account_id: accountId },
      }),
    );
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    setEntries((e) => [...e, ...r.data.data]);
    setMore(r.data.data.length === 50);
  }

  return (
    <>
      <Topbar title={SCREENS.audit.title} sub="Append-only · every surface" />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div className="flex flex-wrap items-center gap-2">
          <nav aria-label="Filter by kind" className="flex flex-wrap gap-1 text-caption">
            {AUDIT_FILTERS.map((f) => (
              <Link key={f.value} href={link(f.value)} aria-current={action === f.value ? "page" : undefined} className="rounded-sm px-2 py-1 aria-[current=page]:bg-active">
                {f.label}
              </Link>
            ))}
          </nav>
          <span className="flex-1" />
          <label className="flex items-center gap-1.5 text-caption">
            <span className="text-fg-subtle">Account</span>
            <select
              className="h-7 rounded-sm border border-border-strong bg-surface px-1.5"
              value={accountId ?? ""}
              onChange={(e) => window.location.assign(link(action, e.target.value || null))}
            >
              <option value="">All</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.nickname}</option>
              ))}
            </select>
          </label>
        </div>
        {error ? <Notice tone="danger" role="alert">{error}</Notice> : null}
        <Panel>
          {entries.length === 0 ? (
            <PanelBody>
              <EmptyState icon="audit" title="Nothing recorded for this filter" />
            </PanelBody>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead className="text-left text-caption text-fg-subtle">
                  <tr>
                    <th scope="col" className="px-3.5 py-2 font-medium">When</th>
                    <th scope="col" className="px-3.5 py-2 font-medium">Who</th>
                    <th scope="col" className="px-3.5 py-2 font-medium">Surface</th>
                    <th scope="col" className="px-3.5 py-2 font-medium">Action</th>
                    <th scope="col" className="px-3.5 py-2 font-medium">Account</th>
                    <th scope="col" className="px-3.5 py-2 font-medium">Text hash</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {entries.map((e) => (
                    <tr key={e.id} className="align-top">
                      <td className="px-3.5 py-2 whitespace-nowrap">
                        <Time value={e.created_at} tz={workspace.timezone} format="datetime" />
                      </td>
                      <td className="px-3.5 py-2">
                        {e.actor_name ?? e.actor_label ?? e.actor_type}
                        {e.actor_name && e.actor_label && e.actor_name !== e.actor_label ? (
                          <span className="block text-caption text-fg-subtle">recorded as “{e.actor_label}”</span>
                        ) : null}
                        {e.actor_type !== "user" ? <span className="block text-caption text-fg-subtle">{e.actor_type}</span> : null}
                      </td>
                      <td className="px-3.5 py-2"><Tag tone={e.surface === "telegram" ? "info" : "neutral"}>{e.surface}</Tag></td>
                      <td className="px-3.5 py-2">
                        <span className={e.action.startsWith("security.") ? "font-medium text-danger" : "font-medium"}>{e.action}</span>
                        {e.after ? (
                          <details className="text-caption text-fg-subtle">
                            <summary className="cursor-pointer">details</summary>
                            <pre className="max-w-[48ch] overflow-x-auto font-mono text-xs whitespace-pre-wrap">{JSON.stringify(e.after, null, 1)}</pre>
                          </details>
                        ) : null}
                      </td>
                      <td className="px-3.5 py-2">{e.social_account_id ? names.get(e.social_account_id) ?? "—" : "—"}</td>
                      <td className="px-3.5 py-2 font-mono text-xs">{e.subject_content_hash ? e.subject_content_hash.slice(0, 16) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        {more ? (
          <Button size="sm" className="justify-self-start" onClick={() => void loadMore()}>
            Load older entries
          </Button>
        ) : null}
      </main>
    </>
  );
}
