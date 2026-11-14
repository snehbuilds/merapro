"use client";

import {
  approveReply,
  deleteComment,
  draftReply,
  editReply,
  hideComment,
  rejectReply,
  submitReply,
  triageComments,
  type CommentOut,
  type ReplyOut,
  type SummaryOut,
} from "@mera/sdk";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useId, useState } from "react";
import { PlatformMark } from "@/components/icons";
import { useSecondFactor } from "@/components/mera/second-factor";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Label, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { SCREENS } from "@/lib/nav";

/*
 * Unified Inbox (P2-M6). Comments on our published posts, across accounts, triaged
 * by rules and the Engagement agent. A reply always goes out as the account that
 * was commented on: the screen shows that account and never offers another. Every
 * reply is approved before it is sent; the approval binds to the exact text.
 * Comment text is from the public and is shown as plain text.
 */

type Platform = Parameters<typeof PlatformMark>[0]["platform"];

const STATE_LABEL: Readonly<Record<string, string>> = {
  new: "New",
  triaged: "Triaged",
  escalated: "Needs a person",
  replied: "Replied",
  hidden: "Hidden",
  ignored: "Ignored",
};
const INTENT_TONE: Readonly<Record<string, "ok" | "warn" | "danger" | "info" | "neutral">> = {
  question: "info",
  praise: "ok",
  complaint: "warn",
  spam: "neutral",
  toxic: "danger",
  other: "neutral",
};
const REPLY_STATE: Readonly<Record<string, string>> = {
  draft: "Draft",
  pending_approval: "Waiting for approval",
  approved: "Approved, sending",
  sending: "Sending",
  sent: "Sent",
  failed: "Failed",
  rejected: "Rejected",
};

export interface InboxScreenProps {
  workspace: { id: string; slug: string; timezone: string; archived: boolean };
  comments: CommentOut[];
  summary: SummaryOut;
  accounts: { id: string; nickname: string; platform: string }[];
  groups: { id: string; name: string }[];
  filters: Record<string, string>;
  canReply: boolean;
  canApprove: boolean;
}

export function InboxScreen({ workspace, comments, summary, accounts, groups, filters, canReply, canApprove }: InboxScreenProps) {
  const id = useId();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { withSecondFactor } = useSecondFactor();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(comments[0]?.id ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const path = { workspace_id: workspace.id };
  const attention = (summary.states.new ?? 0) + (summary.states.escalated ?? 0);
  const current = comments.find((c) => c.id === open) ?? null;

  function filter(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`${pathname}?${next.toString()}`);
  }

  async function act<T>(key: string, work: () => Promise<{ ok: true; data: T } | { ok: false; error: Parameters<typeof displayMessage>[0] }>, ok: string) {
    setBusy(key);
    setFlash(null);
    const r = await work();
    setBusy(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: displayMessage(r.error) });
      return null;
    }
    setFlash({ tone: "info", text: ok });
    router.refresh();
    return r.data;
  }

  async function bulk(state: "triaged" | "ignored" | "escalated" | "new") {
    const ids = [...selected];
    const done = await act("bulk", () => settle(triageComments({ client: browserApi, path, body: { comment_ids: ids, state } })), `${ids.length} marked ${STATE_LABEL[state]?.toLowerCase()}.`);
    if (done) setSelected(new Set());
  }

  function toggle(cid: string, on: boolean) {
    setSelected((s) => {
      const next = new Set(s);
      if (on) next.add(cid);
      else next.delete(cid);
      return next;
    });
  }

  return (
    <>
      <Topbar title={SCREENS.inbox.title} sub={attention ? `${attention} need attention` : "All caught up"} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div role="status" aria-live="polite" className="empty:hidden">
          {flash?.tone === "info" ? <Notice tone="info" icon="check">{flash.text}</Notice> : null}
        </div>
        {flash?.tone === "danger" ? <Notice tone="danger" role="alert">{flash.text}</Notice> : null}

        <div className="flex flex-wrap items-end gap-2.5" role="group" aria-label="Filter comments">
          <FilterSelect id={`${id}-state`} label="State" value={filters.state ?? ""} onChange={(v) => filter("state", v)}
            options={[["", "Open (not deleted)"], ...Object.entries(STATE_LABEL)]} />
          <FilterSelect id={`${id}-group`} label="Group" value={filters.account_group_id ?? ""} onChange={(v) => filter("account_group_id", v)}
            options={[["", "All groups"], ...groups.map((g) => [g.id, g.name] as [string, string])]} />
          <FilterSelect id={`${id}-account`} label="Account" value={filters.social_account_id ?? ""} onChange={(v) => filter("social_account_id", v)}
            options={[["", "All accounts"], ...accounts.map((a) => [a.id, a.nickname] as [string, string])]} />
          <FilterSelect id={`${id}-platform`} label="Platform" value={filters.platform ?? ""} onChange={(v) => filter("platform", v)}
            options={[["", "All platforms"], ...[...new Set(accounts.map((a) => a.platform))].map((p) => [p, p] as [string, string])]} />
          <FilterSelect id={`${id}-sentiment`} label="Sentiment" value={filters.sentiment ?? ""} onChange={(v) => filter("sentiment", v)}
            options={[["", "Any"], ["positive", "Positive"], ["neutral", "Neutral"], ["negative", "Negative"]]} />
        </div>

        {selected.size > 0 && canReply ? (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2.5 text-sm" role="group" aria-label="Bulk triage">
            <span className="font-medium">{selected.size} selected</span>
            <Button size="sm" disabled={busy !== null} onClick={() => void bulk("triaged")}>Mark triaged</Button>
            <Button size="sm" disabled={busy !== null} onClick={() => void bulk("escalated")}>Needs a person</Button>
            <Button size="sm" disabled={busy !== null} onClick={() => void bulk("ignored")}>Ignore</Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
          </div>
        ) : null}

        {comments.length === 0 ? (
          <Panel>
            <PanelBody>
              <EmptyState
                icon="inbox"
                title="No comments here"
                description={`Mera reads comments on your published posts on ${summary.platforms.length ? summary.platforms.join(", ") : "supported platforms"}, every few minutes while a post is fresh.`}
              />
            </PanelBody>
          </Panel>
        ) : (
          <div className="grid gap-3.5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <Panel>
              <PanelHead title={`${comments.length} comment${comments.length === 1 ? "" : "s"}`} />
              <ul className="divide-y divide-border" aria-label="Comments">
                {comments.map((c) => (
                  <li key={c.id} className={c.id === open ? "bg-surface-muted" : undefined}>
                    <div className="flex items-start gap-2.5 px-3.5 py-2.5">
                      {canReply ? (
                        <input
                          type="checkbox"
                          className="mt-1"
                          aria-label={`Select comment from ${c.author_handle ?? "someone"}`}
                          checked={selected.has(c.id)}
                          onChange={(e) => toggle(c.id, e.target.checked)}
                        />
                      ) : null}
                      <button type="button" className="grid min-w-0 flex-1 gap-1 text-left" aria-current={c.id === open} onClick={() => setOpen(c.id)}>
                        <span className="flex items-center gap-1.5 text-caption text-fg-muted">
                          <PlatformMark platform={c.platform as Platform} size="sm" decorative />
                          {c.account_nickname} · @{c.author_handle ?? "someone"}
                          <span className="flex-1" />
                          <Time value={c.created_at_platform ?? c.created_at} tz={workspace.timezone} format="datetime" />
                        </span>
                        <span className="line-clamp-2 text-sm">{c.body}</span>
                        <span className="flex flex-wrap gap-1">
                          <Tag tone={c.state === "escalated" ? "warn" : "neutral"}>{STATE_LABEL[c.state] ?? c.state}</Tag>
                          {c.intent ? <Tag tone={INTENT_TONE[c.intent] ?? "neutral"}>{c.intent}</Tag> : null}
                          {c.replies.some((r) => r.state === "pending_approval") ? <Tag tone="info">Reply waiting</Tag> : null}
                        </span>
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </Panel>

            {current ? (
              <CommentDetail
                key={current.id}
                comment={current}
                workspace={workspace}
                canReply={canReply && !workspace.archived}
                canApprove={canApprove && !workspace.archived}
                busy={busy}
                draft={drafts[current.id] ?? ""}
                setDraft={(v) => setDrafts((d) => ({ ...d, [current.id]: v }))}
                onDraft={(body) =>
                  act("draft", () => settle(draftReply({ client: browserApi, path: { ...path, comment_id: current.id }, body: body ? { body } : {} })), body ? "Draft saved." : "The Engagement agent drafted a reply.").then((d) => {
                    if (d) setDrafts((x) => ({ ...x, [current.id]: "" }));
                  })
                }
                onEdit={(r, body) => act(`edit:${r.id}`, () => settle(editReply({ client: browserApi, path: { ...path, reply_id: r.id }, body: { body } })), "Saved. Approve it again before it is sent.")}
                onSubmit={(r) => act(`submit:${r.id}`, () => settle(submitReply({ client: browserApi, path: { ...path, reply_id: r.id } })), "Sent for approval.")}
                onApprove={(r) =>
                  act(`approve:${r.id}`, () => settle(approveReply({ client: browserApi, path: { ...path, reply_id: r.id }, body: { content_hash: r.content_hash } })), `Approved. Sending as ${current.account_nickname}.`)
                }
                onReject={(r) => act(`reject:${r.id}`, () => settle(rejectReply({ client: browserApi, path: { ...path, reply_id: r.id } })), "Rejected.")}
                onHide={(hidden) => act("hide", () => settle(hideComment({ client: browserApi, path: { ...path, comment_id: current.id }, body: { hidden } })), hidden ? "Hidden on the platform." : "Shown again.")}
                onDelete={() =>
                  act(
                    "delete",
                    () => withSecondFactor((headers) => settle(deleteComment({ client: browserApi, headers, path: { ...path, comment_id: current.id } }))),
                    "Deleted on the platform.",
                  )
                }
              />
            ) : null}
          </div>
        )}
      </main>
    </>
  );
}

function without(map: Record<string, string>, key: string): Record<string, string> {
  const next = { ...map };
  delete next[key];
  return next;
}

function FilterSelect({ id, label, value, onChange, options }: { id: string; label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={id} className="text-caption">
        {label}
      </Label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => (
          <option key={v || "all"} value={v}>
            {l}
          </option>
        ))}
      </Select>
    </div>
  );
}

interface DetailProps {
  comment: CommentOut;
  workspace: { timezone: string };
  canReply: boolean;
  canApprove: boolean;
  busy: string | null;
  draft: string;
  setDraft: (v: string) => void;
  onDraft: (body: string | null) => Promise<void>;
  onEdit: (r: ReplyOut, body: string) => Promise<unknown>;
  onSubmit: (r: ReplyOut) => Promise<unknown>;
  onApprove: (r: ReplyOut) => Promise<unknown>;
  onReject: (r: ReplyOut) => Promise<unknown>;
  onHide: (hidden: boolean) => Promise<unknown>;
  onDelete: () => Promise<unknown>;
}

function CommentDetail({ comment: c, workspace, canReply, canApprove, busy, draft, setDraft, onDraft, onEdit, onSubmit, onApprove, onReject, onHide, onDelete }: DetailProps) {
  const id = useId();
  const [editing, setEditing] = useState<Record<string, string>>({});
  const live = c.replies.filter((r) => r.state !== "rejected");
  return (
    <Panel aria-labelledby={`${id}-head`}>
      <PanelHead
        title={
          <span id={`${id}-head`} className="flex items-center gap-1.5">
            <PlatformMark platform={c.platform as Platform} size="sm" decorative />@{c.author_handle ?? "someone"} on {c.account_nickname}
          </span>
        }
      />
      <PanelBody className="grid gap-3">
        <p className="text-caption text-fg-subtle">
          On your post: <span className="text-fg-muted">{c.post_excerpt}</span>
          {c.post_permalink ? (
            <>
              {" "}
              <a className="font-semibold text-accent underline" href={c.post_permalink} target="_blank" rel="noreferrer">
                Open post
              </a>
            </>
          ) : null}
        </p>
        <blockquote className="whitespace-pre-wrap rounded-md border-l-2 border-border-strong bg-surface-muted px-3 py-2 text-sm">{c.body}</blockquote>
        {c.escalation_reason ? <Notice tone="warn">{c.escalation_reason}</Notice> : null}

        <h3 className="text-sm font-semibold">
          Replies as {c.account_nickname} <span className="font-normal text-fg-subtle">@{c.account_handle}</span>
        </h3>
        {live.length === 0 ? <p className="text-caption text-fg-subtle">No reply yet.</p> : null}
        <ol className="grid gap-2.5">
          {live.map((r) => {
            const edit = editing[r.id];
            const editable = canReply && ["draft", "pending_approval", "failed"].includes(r.state);
            return (
              <li key={r.id} className="grid gap-2 rounded-md border border-border p-2.5">
                <div className="flex flex-wrap items-center gap-1.5 text-caption">
                  <Tag tone={r.state === "sent" ? "ok" : r.state === "failed" ? "danger" : "neutral"}>{REPLY_STATE[r.state] ?? r.state}</Tag>
                  {r.source === "agent" ? <Tag tone="outline">agent{r.confidence !== null ? ` · ${r.confidence.toFixed(2)}` : ""}</Tag> : null}
                  {r.error ? <span className="text-danger">{r.error}</span> : null}
                </div>
                {edit !== undefined ? (
                  <>
                    <Label htmlFor={`${id}-edit-${r.id}`} className="sr-only">
                      Edit reply
                    </Label>
                    <Textarea id={`${id}-edit-${r.id}`} rows={3} maxLength={3000} value={edit} onChange={(e) => setEditing((x) => ({ ...x, [r.id]: e.target.value }))} />
                  </>
                ) : (
                  <p className="whitespace-pre-wrap text-sm">{r.body}</p>
                )}
                <div className="flex flex-wrap gap-2">
                  {edit !== undefined ? (
                    <>
                      <Button size="sm" variant="primary" disabled={busy !== null || !edit.trim()} onClick={() => void onEdit(r, edit).then(() => setEditing((x) => without(x, r.id)))}>
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing((x) => without(x, r.id))}>
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <>
                      {editable ? <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => setEditing((x) => ({ ...x, [r.id]: r.body }))}>Edit</Button> : null}
                      {canReply && (r.state === "draft" || r.state === "failed") ? (
                        <Button size="sm" disabled={busy !== null} onClick={() => void onSubmit(r)}>Ask for approval</Button>
                      ) : null}
                      {canApprove && (r.state === "draft" || r.state === "pending_approval") ? (
                        <>
                          <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => void onApprove(r)}>
                            Approve and send
                          </Button>
                          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void onReject(r)}>Reject</Button>
                        </>
                      ) : null}
                    </>
                  )}
                </div>
                {r.sent_at ? (
                  <p className="text-caption text-fg-subtle">
                    Sent <Time value={r.sent_at} tz={workspace.timezone} format="datetime" />
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>

        {canReply && c.state !== "deleted" ? (
          <div className="grid gap-2">
            <Label htmlFor={`${id}-new`}>New reply as {c.account_nickname}</Label>
            <Textarea id={`${id}-new`} rows={3} maxLength={3000} value={draft} placeholder="Write a reply, or let the Engagement agent draft one" onChange={(e) => setDraft(e.target.value)} />
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="primary" disabled={busy !== null || !draft.trim()} onClick={() => void onDraft(draft.trim())}>
                Save draft
              </Button>
              <Button size="sm" disabled={busy !== null} onClick={() => void onDraft(null)}>
                {busy === "draft" ? "Drafting…" : "Draft with agent"}
              </Button>
              <span className="flex-1" />
              {c.can_hide ? (
                <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void onHide(c.state !== "hidden")}>
                  {c.state === "hidden" ? "Show" : "Hide"}
                </Button>
              ) : null}
              {c.can_delete ? (
                <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void onDelete()}>
                  Delete
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  );
}
