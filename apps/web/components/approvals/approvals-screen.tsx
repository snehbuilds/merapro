"use client";

import { useSecondFactor } from "@/components/mera/second-factor";
import { cancelApprovalRequest, decide, type ApprovalRequestOut } from "@mera/sdk";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { AccountChip } from "@/components/mera/account-chip";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { useWorkspaceEvents } from "@/hooks/use-workspace-events";
import { platformIcon } from "@/lib/accounts";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { displayMessage, type ApiError } from "@/lib/api/errors";
import type { ApprovalRequest, ApprovalVariant } from "@/lib/api/models";
import {
  APPROVAL_ERROR_TEXT,
  REQUEST_STATE_LABEL,
  asApprovalVariantState,
  decidable,
  isOpen,
  quorumLine,
  shownHashes,
} from "@/lib/approvals";
import { composerHref } from "@/lib/composer";
import { mediaContentUrl, readMedia } from "@/lib/media";
import { SCREENS } from "@/lib/nav";

/*
 * Approval Queue (approvals.html, M6). One card per post set, one row per account:
 * the exact text each account would publish (the version the approval covers),
 * per-account Approve and Reject, and "Approve all pending" for the card. A
 * decision is bound to what is on screen: the API refuses it if any post changed.
 * Bot cards arrive with M7; this is the same decision path.
 */

export interface ApprovalsScreenProps {
  workspace: { id: string; slug: string; timezone: string; archived: boolean };
  requests: ApprovalRequest[];
}

type Flash = { tone: "info" | "danger"; text: string };

function errorText(error: ApiError): string {
  return APPROVAL_ERROR_TEXT[error.code] ?? displayMessage(error);
}

export function ApprovalsScreen({ workspace, requests: fromServer }: ApprovalsScreenProps) {
  const router = useRouter();
  const { withSecondFactor } = useSecondFactor();
  const [state, setState] = useState({ fromServer, list: fromServer });
  if (state.fromServer !== fromServer) setState({ fromServer, list: fromServer });
  const list = state.list;
  const [flash, setFlash] = useState<Flash | null>(null);
  // Decisions from Telegram (or another tab) arrive over SSE and refresh this queue.
  useWorkspaceEvents(workspace.id, ["approval.*"]);
  const open = list.filter(isOpen);
  const decided = list.filter((r) => !isOpen(r));

  function replace(next: ApprovalRequestOut) {
    setState((s) => ({ ...s, list: s.list.map((r) => (r.id === next.id ? next : r)) }));
  }

  async function act(req: ApprovalRequest, action: "approve" | "reject", variants: ApprovalVariant[] | null, reason?: string) {
    setFlash(null);
    const shown = variants ?? decidable(req);
    // A company page needs a second factor (P2-M2.5); each attempt gets its own idempotency key.
    const r = await withSecondFactor((extra) =>
      settle(
        decide({
          client: browserApi,
          headers: { ...idempotencyHeaders(), ...extra },
          path: { workspace_id: workspace.id, request_id: req.id },
          body: {
            action,
            variant_scope: variants ? variants.map((v) => v.post_variant_id) : shown.map((v) => v.post_variant_id),
            content_hashes: shownHashes(shown),
            reason: reason ?? null,
          },
        }),
      ),
    );
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return false;
    }
    replace(r.data);
    const names = shown.map((v) => v.nickname).join(", ");
    setFlash({ tone: "info", text: `${action === "approve" ? "Approved" : "Rejected"}: ${names}.` });
    router.refresh();
    return true;
  }

  async function withdraw(req: ApprovalRequest) {
    setFlash(null);
    const r = await settle(
      cancelApprovalRequest({ client: browserApi, path: { workspace_id: workspace.id, request_id: req.id }, body: { reason: null } }),
    );
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return;
    }
    replace(r.data);
    setFlash({ tone: "info", text: "Withdrawn. Accounts already decided keep their outcome." });
    router.refresh();
  }

  const pendingAccounts = open.reduce((n, r) => n + r.variants.filter((v) => v.state === "pending").length, 0);

  return (
    <>
      <Topbar
        title={SCREENS.approvals.title}
        sub={open.length ? `${pendingAccounts} ${pendingAccounts === 1 ? "account" : "accounts"} across ${open.length} ${open.length === 1 ? "post" : "posts"}` : "Nothing waiting"}
      />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div role="status" aria-live="polite" className="empty:hidden">
          {flash?.tone === "info" ? (
            <Notice tone="info" icon="check">
              {flash.text}
            </Notice>
          ) : null}
        </div>
        {flash?.tone === "danger" ? (
          <Notice tone="danger" role="alert">
            {flash.text}{" "}
            <button type="button" className="font-semibold underline" onClick={() => router.refresh()}>
              Reload
            </button>
          </Notice>
        ) : null}

        {open.length === 0 ? (
          <Panel>
            <PanelBody>
              <EmptyState
                icon="check"
                title="Nothing waiting for approval"
                description="Posts appear here when someone sends them from the Composer. Each account is decided on its own."
              />
            </PanelBody>
          </Panel>
        ) : (
          open.map((req) => (
            <RequestCard
              key={req.id}
              req={req}
              workspace={workspace}
              onDecide={act}
              onWithdraw={() => void withdraw(req)}
            />
          ))
        )}

        {decided.length ? (
          <section aria-labelledby="decided-head" className="grid gap-2">
            <h2 id="decided-head" className="font-label text-caption font-semibold tracking-[0.09em] uppercase text-fg-subtle">
              Recently decided
            </h2>
            <Panel>
              <ul className="divide-y divide-border">
                {decided.map((req) => (
                  <li key={req.id} className="flex flex-wrap items-center gap-2.5 px-3.5 py-2.5 text-sm">
                    <Link href={composerHref(workspace.slug, { set: req.post_set_id })} className="min-w-0 flex-1 truncate font-medium hover:underline">
                      {req.title ?? "Untitled post"}
                    </Link>
                    <Tag tone={req.state === "approved" ? "ok" : req.state === "partially_approved" ? "info" : "neutral"}>
                      {REQUEST_STATE_LABEL[req.state] ?? req.state}
                    </Tag>
                    <span className="text-caption text-fg-subtle">
                      {req.variants.filter((v) => v.state === "approved").length} of {req.variants.length} approved
                    </span>
                    {req.decided_at ? <Time value={req.decided_at} tz={workspace.timezone} format="datetime" className="text-caption text-fg-subtle" /> : null}
                  </li>
                ))}
              </ul>
            </Panel>
          </section>
        ) : null}
      </main>
    </>
  );
}

function RequestCard({
  req,
  workspace,
  onDecide,
  onWithdraw,
}: {
  req: ApprovalRequest;
  workspace: ApprovalsScreenProps["workspace"];
  onDecide: (req: ApprovalRequest, action: "approve" | "reject", variants: ApprovalVariant[] | null, reason?: string) => Promise<boolean>;
  onWithdraw: () => void;
}) {
  const id = useId();
  const [busy, setBusy] = useState<string | null>(null);
  const mine = decidable(req);
  const locked = workspace.archived;

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    await fn();
    setBusy(null);
  }

  return (
    <Panel aria-labelledby={`${id}-title`}>
      <PanelHead
        actions={
          <>
            <Button size="sm" variant="ghost" disabled={busy !== null || locked} onClick={onWithdraw}>
              Withdraw
            </Button>
            {mine.length > 1 ? (
              <Button size="sm" variant="primary" disabled={busy !== null || locked} onClick={() => void run("all", () => onDecide(req, "approve", null))}>
                {busy === "all" ? "Approving…" : `Approve all ${mine.length}`}
              </Button>
            ) : null}
          </>
        }
      >
        <div className="min-w-0">
          <h2 id={`${id}-title`} className="truncate text-md font-semibold">
            <Link href={composerHref(workspace.slug, { set: req.post_set_id })} className="hover:underline">
              {req.title ?? "Untitled post"}
            </Link>
          </h2>
          <p className="text-caption text-fg-subtle">
            {req.requested_by ? `Sent by ${req.requested_by} · ` : ""}
            expires <Time value={req.expires_at} tz={workspace.timezone} format="datetime" />
          </p>
        </div>
      </PanelHead>
      <PanelBody className="grid gap-3">
        {req.variants.map((v) => (
          <VariantRow key={v.post_variant_id} v={v} workspaceId={workspace.id} busy={busy} locked={locked} onDecide={(action, reason) => run(`${action}:${v.post_variant_id}`, () => onDecide(req, action, [v], reason))} />
        ))}
      </PanelBody>
      <PanelFoot>
        Each account is decided on its own. Expiry never publishes; approving binds to the text shown here.
      </PanelFoot>
    </Panel>
  );
}

function VariantRow({
  v,
  workspaceId,
  busy,
  locked,
  onDecide,
}: {
  v: ApprovalVariant;
  workspaceId: string;
  busy: string | null;
  locked: boolean;
  onDecide: (action: "approve" | "reject", reason?: string) => Promise<unknown>;
}) {
  const id = useId();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const media = readMedia(v.media);
  const q = quorumLine(v);
  const flags = v.compliance_flags.filter((f) => f.severity === "warning" || f.severity === "block");
  return (
    <article aria-labelledby={`${id}-who`} className="grid gap-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <AccountChip id={`${id}-who`} account={{ platform: platformIcon(v.platform as never), name: v.nickname, handle: v.handle }} />
        <StatusPill kind="approval_variant" state={asApprovalVariantState(v.state)} />
        {q ? <span className="text-caption text-fg-subtle">{q}</span> : null}
        {v.my_vote === "approve" && v.state === "pending" ? <Tag tone="info">You approved</Tag> : null}
        {!v.is_current ? <Tag tone="warn">Edited since</Tag> : null}
        <span className="flex-1" />
        {v.confidence_score !== null ? <span className="font-mono text-caption text-fg-subtle">confidence {v.confidence_score.toFixed(2)}</span> : null}
      </div>
      <p className="text-sm whitespace-pre-wrap">
        {v.thread_parts?.length ? v.thread_parts.join("\n\n—\n\n") : v.body}
        {v.hashtags.length ? <span className="text-accent"> {v.hashtags.map((t) => `#${t}`).join(" ")}</span> : null}
      </p>
      {v.link_url ? <p className="truncate text-caption text-fg-subtle">Link: {v.link_url}</p> : null}
      {media.length ? (
        <div className="flex flex-wrap gap-2">
          {media.map((m) => (
            <figure key={m.assetId ?? m.masterId} className="grid max-w-40 gap-1">
              {/* eslint-disable-next-line @next/next/no-img-element -- authenticated, same-origin bytes */}
              <img
                src={mediaContentUrl(workspaceId, m.assetId ?? m.masterId)}
                alt={m.altText ?? ""}
                className="h-24 w-auto rounded-sm border border-border object-cover"
              />
              <figcaption className="text-caption text-fg-subtle">{m.altText ? `Alt: ${m.altText}` : "No alt text"}</figcaption>
            </figure>
          ))}
        </div>
      ) : null}
      {flags.length ? (
        <ul className="grid gap-1 text-caption text-warn">
          {flags.map((f, i) => (
            <li key={i}>⚑ {String(f.message ?? f.code ?? "Editor flag")}</li>
          ))}
        </ul>
      ) : null}
      {v.can_decide ? (
        rejecting ? (
          <form
            className="flex flex-wrap items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void onDecide("reject", reason.trim() || undefined).then(() => setRejecting(false));
            }}
          >
            <label htmlFor={`${id}-reason`} className="sr-only">
              Why reject {v.nickname}?
            </label>
            <input
              id={`${id}-reason`}
              value={reason}
              maxLength={1000}
              placeholder="Reason (optional, helps the writer)"
              onChange={(e) => setReason(e.target.value)}
              className="h-8 min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-2.5 text-sm"
            />
            <Button size="sm" variant="danger" type="submit" disabled={busy !== null || locked}>
              Reject {v.nickname}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" disabled={busy !== null || locked} onClick={() => void onDecide("approve")}>
              {busy === `approve:${v.post_variant_id}` ? "Approving…" : `Approve ${v.nickname}`}
            </Button>
            <Button size="sm" disabled={busy !== null || locked} onClick={() => setRejecting(true)}>
              Reject…
            </Button>
          </div>
        )
      ) : null}
    </article>
  );
}
