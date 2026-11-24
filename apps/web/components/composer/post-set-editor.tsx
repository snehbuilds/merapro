"use client";

import {
  archivePostSet,
  createVersion,
  generatePostSet,
  orchestratePostSet,
  getPostSet,
  regenerateVariant,
  requestApproval,
  rewriteVariant,
  setVariantMedia,
  suggestHashtags,
  updateVariant,
  type AccountOut,
} from "@mera/sdk";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useState, useSyncExternalStore } from "react";
import { isWebUrl } from "@/components/discovery/trending-card";
import { AccountMark } from "@/components/mera/account-chip";
import { StaggerTimeline, formatOffset } from "@/components/mera/stagger-timeline";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Tag } from "@/components/ui/tag";
import { platformIcon } from "@/lib/accounts";
import { browserApi, idempotencyHeaders, settle, type Result } from "@/lib/api/browser";
import { displayMessage, type ApiError } from "@/lib/api/errors";
import type { HashtagSuggestion, PostSet, Variant, Voice } from "@/lib/api/models";
import type { MediaChoice } from "@/lib/media";
import { APPROVAL_ERROR_TEXT } from "@/lib/approvals";
import {
  POLL_INTERVAL_MS,
  POST_SET_ERROR_TEXT,
  asPostSetState,
  asVariantState,
  composerHref,
  confidenceTone,
  crossAccountFindings,
  crossAccountText,
  excerpt,
  forgetSkipped,
  generationText,
  isGenerating,
  isSetArchivable,
  orderedVariants,
  parseSkipped,
  readSkippedRaw,
  readSuggestions,
  setSummary,
  skippedText,
} from "@/lib/composer";
import { cn } from "@/lib/utils";
import { ExperimentsPanel } from "./experiments-panel";
import { VariantEditor, type ActionResult } from "./variant-editor";

/*
 * composer.html for one post set: the accounts it targets (each drafted,
 * approved, scheduled and published on its own), the stored publishing
 * order, the selected account's editor and preview, and a commit bar whose
 * verdicts come from the stored guardrails. While a drafting job is queued
 * or running the set is polled every ~2s until it settles.
 */

export interface PostSetEditorProps {
  workspace: { id: string; slug: string; timezone: string };
  postSet: PostSet;
  accounts: readonly Pick<AccountOut, "id" | "nickname">[];
  voices: Voice[] | null;
  /** Editor+ and the workspace isn't archived. */
  canEdit: boolean;
  /** Poll interval while drafting; tests shorten it. */
  pollMs?: number;
}

function errorText(error: ApiError): string {
  return POST_SET_ERROR_TEXT[error.code] ?? displayMessage(error);
}

const noopSubscribe = () => () => {};

/** Keep a local copy so actions show at once; a new server copy (navigation, refresh) replaces it. */
function useLocalSet(fromServer: PostSet) {
  const [state, setState] = useState({ fromServer, set: fromServer });
  if (state.fromServer !== fromServer) setState({ fromServer, set: fromServer });
  const update = useCallback((set: PostSet) => setState((s) => ({ ...s, set })), []);
  return [state.set, update] as const;
}

export function PostSetEditor({ workspace, postSet, accounts, voices, canEdit: canManage, pollMs = POLL_INTERVAL_MS }: PostSetEditorProps) {
  const router = useRouter();
  const [set, setSet] = useLocalSet(postSet);
  const ordered = orderedVariants(set.variants);
  const [selectedId, setSelectedId] = useState<string | undefined>(() => (ordered.find((v) => v.enabled) ?? ordered[0])?.id);
  const [pollError, setPollError] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [skippedDismissed, setSkippedDismissed] = useState(false);
  const id = useId();

  const archived = set.state === "archived";
  const canEdit = canManage && !archived;
  const generating = isGenerating(set);
  const selected = ordered.find((v) => v.id === selectedId) ?? ordered[0];
  const enabled = ordered.filter((v) => v.enabled);
  const findings = crossAccountFindings(set);
  const summary = setSummary(set);
  const progress = generationText(set);
  const skippedRaw = useSyncExternalStore(noopSubscribe, () => readSkippedRaw(set.id), () => null);
  const skippedNote = skippedDismissed ? null : skippedText(parseSkipped(skippedRaw), accounts);
  const groupNames = [...new Set(set.variants.map((v) => v.group_name).filter((g): g is string => !!g))];

  // Poll while a drafting job is queued or running; stop as soon as it settles (idle or failed).
  const shouldPoll = generating && pollError === null;
  useEffect(() => {
    if (!shouldPoll) return;
    let live = true;
    const timer = setTimeout(async () => {
      const r = await settle(getPostSet({ client: browserApi, path: { workspace_id: workspace.id, set_id: set.id } }));
      if (!live) return;
      if (r.ok) setSet(r.data);
      else setPollError(errorText(r.error));
    }, pollMs);
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // `set` is a dependency on purpose: each fresh copy schedules the next poll.
  }, [shouldPoll, set, pollMs, workspace.id, setSet]);

  const path = (v: Variant) => ({ workspace_id: workspace.id, set_id: set.id, variant_id: v.id });

  async function act<T>(call: Promise<Result<T>>, onData?: (data: T) => void): Promise<ActionResult> {
    const r = await call;
    if (!r.ok) return { ok: false, message: errorText(r.error), code: r.error.code };
    onData?.(r.data);
    return { ok: true };
  }

  const handlers = (v: Variant) => ({
    onSave: (content: {
      body: string;
      thread_parts: string[] | null;
      hashtags: string[];
      first_comment: string | null;
    }) =>
      act(
        settle(
          createVersion({
            client: browserApi,
            path: path(v),
            body: {
              body: content.body,
              thread_parts: content.thread_parts,
              hashtags: content.hashtags,
              link_url: v.current_version?.link_url ?? null,
              first_comment: content.first_comment,
              based_on_version_id: v.current_version?.id ?? null,
            },
          }),
        ),
        setSet,
      ),
    onRewrite: (instruction: string) =>
      act(settle(rewriteVariant({ client: browserApi, path: path(v), body: { instruction } })), (d) => {
        setPollError(null);
        setSet(d);
      }),
    onRegenerate: () =>
      act(settle(regenerateVariant({ client: browserApi, path: path(v) })), (d) => {
        setPollError(null);
        setSet(d);
      }),
    onDraft: () =>
      act(
        settle(generatePostSet({ client: browserApi, path: { workspace_id: workspace.id, set_id: set.id }, body: { variant_ids: [v.id] } })),
        (d) => {
          setPollError(null);
          setSet(d);
        },
      ),
    onSuggest: async (): Promise<ActionResult<HashtagSuggestion[]>> => {
      const r = await settle(suggestHashtags({ client: browserApi, path: path(v) }));
      if (!r.ok) return { ok: false, message: errorText(r.error), code: r.error.code };
      return { ok: true, data: readSuggestions(r.data.data) };
    },
    onToggle: (on: boolean) =>
      act(settle(updateVariant({ client: browserApi, path: path(v), body: { enabled: on } })), (d) => {
        setSet(d);
        setFlash({ tone: "info", text: on ? `${v.nickname} is back in this post.` : `${v.nickname} is off for this post. Its history is kept.` });
      }),
    onVoice: (voiceId: string | null) => act(settle(updateVariant({ client: browserApi, path: path(v), body: { brand_voice_id: voiceId } })), setSet),
    onMedia: (choices: MediaChoice[]) =>
      act(
        settle(
          setVariantMedia({
            client: browserApi,
            path: path(v),
            body: { media: choices, based_on_version_id: v.current_version?.id ?? "" },
          }),
        ),
        setSet,
      ),
  });

  async function retryDrafting() {
    setFlash(null);
    const r = await settle(generatePostSet({ client: browserApi, path: { workspace_id: workspace.id, set_id: set.id } }));
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return;
    }
    setPollError(null);
    setSet(r.data);
  }

  // P2-M7.3: draft, illustrate and propose times for every account as one run.
  async function orchestrate() {
    setFlash(null);
    const r = await settle(orchestratePostSet({ client: browserApi, path: { workspace_id: workspace.id, set_id: set.id } }));
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return;
    }
    router.push(`/w/${workspace.slug}/agents?run=${r.data.run_id}`);
  }

  async function archive() {
    setArchiving(true);
    setFlash(null);
    const r = await settle(archivePostSet({ client: browserApi, path: { workspace_id: workspace.id, set_id: set.id } }));
    setArchiving(false);
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return;
    }
    setConfirmArchive(false);
    router.push(composerHref(workspace.slug));
    router.refresh();
  }

  const approvalNoteId = `${id}-approval-note`;
  const [sending, setSending] = useState(false);
  const inApproval = set.state === "needs_approval";
  const readyToSend =
    canEdit &&
    !generating &&
    !inApproval &&
    // The API takes a whole set of fresh drafts: a rejected or approved account is edited
    // first (a new version), which puts it back in drafting.
    (set.state === "drafting" || set.state === "rejected" || set.state === "partially_approved") &&
    enabled.length > 0 &&
    enabled.every((v) => v.current_version && v.state === "drafting");

  async function sendForApproval() {
    setSending(true);
    setFlash(null);
    const r = await settle(
      requestApproval({ client: browserApi, headers: idempotencyHeaders(), path: { workspace_id: workspace.id, set_id: set.id }, body: {} }),
    );
    if (!r.ok) {
      setSending(false);
      setFlash({ tone: "danger", text: APPROVAL_ERROR_TEXT[r.error.code] ?? displayMessage(r.error) });
      return;
    }
    const fresh = await settle(getPostSet({ client: browserApi, path: { workspace_id: workspace.id, set_id: set.id } }));
    setSending(false);
    if (fresh.ok) setSet(fresh.data);
    setFlash({
      tone: "info",
      text: `Sent ${r.data.variants.length} ${r.data.variants.length === 1 ? "account" : "accounts"} for approval. Each is decided on its own in Approvals.`,
    });
  }
  const scheduleNoteId = `${id}-schedule-note`;

  return (
    <div className="grid gap-3.5">
      <div role="status" aria-live="polite" className="grid gap-2 empty:hidden">
        {progress ? (
          <Notice tone="info" icon="clock">
            {progress} Each account fills in as it is written.
          </Notice>
        ) : null}
        {flash?.tone === "info" ? (
          <Notice tone="info" icon="check">
            {flash.text}
          </Notice>
        ) : null}
      </div>
      {flash?.tone === "danger" ? (
        <Notice tone="danger" role="alert">
          {flash.text}
        </Notice>
      ) : null}
      {pollError ? (
        <Notice
          tone="warn"
          title="Lost track of drafting."
          actions={
            <Button size="sm" onClick={() => setPollError(null)}>
              Check again
            </Button>
          }
        >
          {pollError}
        </Notice>
      ) : null}
      {set.generation_status === "failed" ? (
        <Notice
          tone="danger"
          title="Drafting failed."
          actions={
            canEdit ? (
              <Button size="sm" onClick={() => void retryDrafting()}>
                Try drafting again
              </Button>
            ) : undefined
          }
        >
          {set.generation_error ?? "The Writer didn't finish."} Accounts that were drafted keep their text.
        </Notice>
      ) : null}
      {skippedNote ? (
        <Notice
          tone="warn"
          actions={
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                forgetSkipped(set.id);
                setSkippedDismissed(true);
              }}
            >
              Dismiss
            </Button>
          }
        >
          {skippedNote}
        </Notice>
      ) : null}
      {findings.map((f) => (
        <Notice
          key={`${f.a.id}:${f.b.id}`}
          tone={f.outcome === "block" ? "danger" : "warn"}
          title={f.outcome === "block" ? "Two accounts read almost the same." : "Two accounts are close."}
        >
          {crossAccountText(f)}
        </Notice>
      ))}

      <Panel aria-labelledby={`${id}-title`}>
        <PanelHead className="flex-wrap">
          <h2 id={`${id}-title`} className="min-w-0 truncate text-md font-semibold">
            {set.title}
          </h2>
          <StatusPill kind="post_set" state={asPostSetState(set.state)} />
          <span className="flex-1" />
          {canEdit && isSetArchivable(set) ? (
            <Button size="sm" onClick={() => void orchestrate()} title="Draft, add the images a platform needs, and propose times, as one run">
              Run all agents
            </Button>
          ) : null}
          {canEdit && isSetArchivable(set) ? (
            <Button size="sm" variant="ghost" onClick={() => setConfirmArchive(true)} disabled={archiving}>
              Archive
            </Button>
          ) : null}
        </PanelHead>
        <PanelBody className="grid gap-1.5 text-sm text-fg-muted">
          {set.content ? (
            <p>
              From{" "}
              {isWebUrl(set.content.url) ? (
                <a href={set.content.url} target="_blank" rel="noopener noreferrer" className="font-medium text-accent hover:underline">
                  {set.content.title}
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              ) : (
                <span className="font-medium text-fg">{set.content.title}</span>
              )}
              {set.content.source_name ? ` · ${set.content.source_name}` : ""}
            </p>
          ) : set.topic_brief ? (
            <p>
              <span className="font-medium text-fg">Brief: </span>
              {set.topic_brief}
            </p>
          ) : null}
          <p className="text-caption text-fg-subtle">
            {enabled.length} of {set.variants.length} {set.variants.length === 1 ? "account" : "accounts"} on
            {groupNames.length ? ` · via ${groupNames.join(", ")}` : ""}
          </p>
          {confirmArchive ? (
            <Notice
              tone="danger"
              title="Archive this post set?"
              actions={
                <>
                  <Button size="sm" variant="danger" onClick={() => void archive()} disabled={archiving}>
                    {archiving ? "Archiving…" : "Archive post set"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmArchive(false)} disabled={archiving}>
                    Cancel
                  </Button>
                </>
              }
            >
              It leaves Recent drafts. Every version is kept.
            </Notice>
          ) : null}
        </PanelBody>
      </Panel>

      <div className="grid grid-cols-[300px_minmax(0,1fr)] items-start gap-3.5 max-wide:grid-cols-[260px_minmax(0,1fr)] max-rail:grid-cols-1">
        <div className="grid gap-3.5">
          <Panel aria-labelledby={`${id}-accounts`}>
            <PanelHead>
              <h2 id={`${id}-accounts`} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
                Accounts
              </h2>
              <span className="flex-1" />
              <Tag tone="outline">{enabled.length} enabled</Tag>
            </PanelHead>
            <ul aria-labelledby={`${id}-accounts`}>
              {ordered.map((v) => (
                <li key={v.id}>
                  <AccountRow variant={v} selected={v.id === selected?.id} generating={generating} onSelect={() => setSelectedId(v.id)} />
                </li>
              ))}
            </ul>
            <PanelFoot>Each account is drafted, approved, scheduled and published on its own.</PanelFoot>
          </Panel>

          <Panel aria-labelledby={`${id}-order`}>
            <PanelHead>
              <h2 id={`${id}-order`} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
                Publishing order
              </h2>
            </PanelHead>
            <PanelBody className="grid gap-2">
              {enabled.length ? (
                <StaggerTimeline
                  tz={workspace.timezone}
                  items={enabled.map((v) => ({ id: v.id, label: v.nickname, offsetSeconds: v.stagger_offset_seconds }))}
                />
              ) : (
                <p className="text-sm text-fg-muted">No accounts are on for this post.</p>
              )}
              <p className="text-caption text-fg-subtle">
                Offsets come from each group&apos;s stagger settings and are stored now, so the calendar and any retry agree with what
                you approved. Picking a time arrives with scheduling (M8).
              </p>
            </PanelBody>
          </Panel>
        </div>

        <div className="grid min-w-0 gap-3.5">
          {selected ? (
            <VariantEditor
              key={`${selected.id}:${selected.current_version?.id ?? "none"}`}
              workspaceId={workspace.id}
              contentItemId={set.content?.id ?? null}
              variant={selected}
              voices={voices}
              canEdit={canEdit}
              generating={generating}
              {...handlers(selected)}
            />
          ) : (
            <Panel>
              <PanelBody>
                <p className="text-sm text-fg-muted">This post set has no accounts.</p>
              </PanelBody>
            </Panel>
          )}

          <ExperimentsPanel workspace={workspace} set={set} variantId={selected?.id ?? null} canEdit={canEdit} />

          <Panel aria-label="Commit">
            <PanelBody className="flex flex-wrap items-center justify-between gap-3.5">
              <div className="flex min-w-0 flex-wrap items-center gap-2.5">
                <StatusPill tone={summary.distinct.tone} shape={summary.distinct.tone === "neutral" ? "ring" : "full"} label={summary.distinct.label} />
                <StatusPill
                  tone={summary.guardrails.tone}
                  shape={summary.guardrails.tone === "neutral" ? "ring" : "full"}
                  label={summary.guardrails.label}
                />
              </div>
              <div className="flex flex-none flex-wrap items-center gap-2">
                <Button disabled aria-describedby={scheduleNoteId}>
                  Schedule…
                </Button>
                {inApproval ? (
                  <Link
                    href={`/w/${workspace.slug}/approvals`}
                    className="inline-flex h-8 items-center rounded-md border border-accent bg-accent px-3 text-sm font-semibold text-accent-fg hover:bg-accent-hover"
                  >
                    Waiting in Approvals
                  </Link>
                ) : (
                  <Button variant="primary" disabled={!readyToSend || sending} aria-describedby={approvalNoteId} onClick={() => void sendForApproval()}>
                    {sending ? "Sending…" : `Send ${enabled.length} for approval`}
                  </Button>
                )}
              </div>
            </PanelBody>
            <PanelFoot className="grid gap-0.5">
              <p id={scheduleNoteId}>Scheduling arrives with the calendar (M8).</p>
              <p id={approvalNoteId}>
                {inApproval
                  ? "Waiting for approval. Editing an account's text takes that account out of the request; the others stay."
                  : "Each account is approved on its own, for the exact text saved here. Guardrails run again before it is sent."}
              </p>
            </PanelFoot>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function AccountRow({ variant: v, selected, generating, onSelect }: { variant: Variant; selected: boolean; generating: boolean; onSelect: () => void }) {
  const version = v.current_version;
  const thread = version?.thread_parts?.length ? version.thread_parts.length : 0;
  return (
    <button
      type="button"
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "grid w-full gap-[5px] border-b border-border px-3.5 py-3 text-left hover:bg-surface-hover",
        "aria-[current=true]:bg-accent-soft",
      )}
    >
      <span className="flex items-center gap-2">
        <AccountMark platform={platformIcon(v.platform)} className={cn(!v.enabled && "opacity-55")} />
        <span className={cn("min-w-0 truncate text-sm font-medium", selected && "font-semibold")}>{v.nickname}</span>
        <span className="flex-1" />
        {!v.enabled ? (
          <StatusPill tone="neutral" shape="ring" label="Off" />
        ) : version?.confidence_score !== null && version?.confidence_score !== undefined ? (
          <Tag tone="outline" title="Editor confidence" className={cn(confidenceTone(version.confidence_score) === "danger" && "text-danger")}>
            <span className="sr-only">Confidence </span>
            {version.confidence_score.toFixed(2)}
          </Tag>
        ) : null}
      </span>
      <span className={cn("line-clamp-2 text-sm text-fg-muted", !v.enabled && "text-fg-subtle line-through")}>
        {!v.enabled ? (
          <>
            <span className="sr-only">Off: </span>
            Off for this post — drops out of the set without losing its history.
          </>
        ) : version ? (
          excerpt(version)
        ) : generating ? (
          "Drafting…"
        ) : (
          "Not drafted yet"
        )}
      </span>
      {v.enabled ? (
        <span className="flex flex-wrap items-center gap-2 text-xs text-fg-subtle">
          <span className="tabular font-mono">{formatOffset(v.stagger_offset_seconds * 1000)}</span>
          {version ? (
            <>
              <span aria-hidden="true">·</span>
              <span className="tabular font-mono">{thread ? `Thread · ${thread}` : `${version.char_count} / ${version.char_limit}`}</span>
            </>
          ) : null}
          <span aria-hidden="true">·</span>
          <StatusPill kind="post_variant" state={asVariantState(v.state)} label={v.state === "drafting" ? "Draft" : undefined} />
        </span>
      ) : null}
    </button>
  );
}
