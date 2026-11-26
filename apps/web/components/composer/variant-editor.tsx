"use client";

import { useId, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import { AccountChip } from "@/components/mera/account-chip";
import { CharCounter } from "@/components/mera/char-counter";
import { PlatformPreview, type PreviewMedia } from "@/components/mera/platform-preview";
import { MediaPanel } from "./media-panel";
import { Button } from "@/components/ui/button";
import { DescriptionList, type DescriptionItem } from "@/components/ui/description-list";
import { EmptyState } from "@/components/ui/empty-state";
import { Input, Label, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tag } from "@/components/ui/tag";
import { platformIcon, platformLabel } from "@/lib/accounts";
import type { HashtagSuggestion, Variant, Voice } from "@/lib/api/models";
import {
  HOOK_TYPE_LABEL,
  MAX_THREAD_PART_CHARS,
  MIN_POSTS_FOR_LIFT,
  applicableVoices,
  isVariantEditable,
  REWRITE_CHIPS,
  basisLabel,
  composeText,
  confidenceTone,
  conventionLine,
  formatLift,
  issueCategoryLabel,
  normalizeHashtag,
  readIssues,
  readNotes,
  readSuggestions,
  readValidation,
  variantGuardrailReasons,
  voiceSourceLabel,
  weightedLength,
} from "@/lib/composer";
import { mediaContentUrl, readMedia, type MediaChoice } from "@/lib/media";
import { SCOPE_LABEL } from "@/lib/voices";
import { cn } from "@/lib/utils";

/*
 * composer.html right column for the selected account: its text (a new
 * immutable version on every save), the quick-rewrite chips, hashtags with
 * their evidence, the gated media panel, why the agent wrote what it wrote,
 * and the live preview. The draft is local until saved; a new version from
 * the server (a save, a rewrite) remounts this component with fresh text.
 */

export type ActionResult<T = undefined> = { ok: true; data?: T } | { ok: false; message: string; code: string };

export interface VariantEditorProps {
  workspaceId: string;
  /** The post set's article, when drafted from one (its preview image is offered). */
  contentItemId: string | null;
  variant: Variant;
  voices: readonly Voice[] | null;
  /** Editor+, the workspace isn't archived, and the set isn't archived. */
  canEdit: boolean;
  /** A drafting job is queued or running for this set. */
  generating: boolean;
  onSave: (content: {
    body: string;
    thread_parts: string[] | null;
    hashtags: string[];
    first_comment: string | null;
  }) => Promise<ActionResult>;
  onRewrite: (instruction: string) => Promise<ActionResult>;
  onRegenerate: () => Promise<ActionResult>;
  onDraft: () => Promise<ActionResult>;
  onSuggest: () => Promise<ActionResult<HashtagSuggestion[]>>;
  onToggle: (enabled: boolean) => Promise<ActionResult>;
  onVoice: (voiceId: string | null) => Promise<ActionResult>;
  onMedia: (choices: MediaChoice[]) => Promise<ActionResult>;
}

const TOGGLEABLE: readonly string[] = ["drafting", "blocked", "skipped"];

function Divider() {
  return <div className="my-3.5 h-px bg-border" />;
}

function Dash({ sr }: { sr: string }) {
  return (
    <span className="text-fg-subtle">
      <span aria-hidden="true">—</span>
      <span className="sr-only">{sr}</span>
    </span>
  );
}

export function VariantEditor({
  workspaceId,
  contentItemId,
  variant,
  voices,
  canEdit,
  generating,
  onSave,
  onRewrite,
  onRegenerate,
  onDraft,
  onSuggest,
  onToggle,
  onVoice,
  onMedia,
}: VariantEditorProps) {
  const version = variant.current_version;
  const thread = version?.thread_parts && version.thread_parts.length > 0 ? version.thread_parts : null;
  const [body, setBody] = useState(version?.body ?? "");
  const [parts, setParts] = useState<string[]>(thread ? [...thread] : []);
  const [tags, setTags] = useState<string[]>(version?.hashtags ?? []);
  const [firstComment, setFirstComment] = useState(version?.first_comment ?? "");
  const [newTag, setNewTag] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ message: string; code: string } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<HashtagSuggestion[] | null>(() => {
    const stored = readSuggestions(version?.hashtag_suggestions ?? []);
    return stored.length ? stored : null;
  });
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [voiceChoice, setVoiceChoice] = useState(variant.brand_voice_id ?? "");
  const [viewport, setViewport] = useState<"desktop" | "mobile">("desktop");
  const id = useId();

  const locked = !isVariantEditable(variant);
  const editable = canEdit && !locked && !generating && variant.enabled && !!version;
  const notes = readNotes(version);
  const issues = readIssues(version);
  const validation = readValidation(version);
  const reasons = variantGuardrailReasons(variant);
  const label = platformLabel(variant.platform);
  const limit = version?.char_limit ?? 0;
  const dirty =
    !!version &&
    (body !== version.body ||
      (thread ? parts.some((p, i) => p !== thread[i]) : false) ||
      tags.join(" ") !== version.hashtags.join(" ") ||
      firstComment !== (version.first_comment ?? ""));
  const sourceLabel = variant.brand_voice_id ? voiceSourceLabel("variant") : voiceSourceLabel(notes.voiceSource);

  async function run<T>(key: string, action: () => Promise<ActionResult<T>>, done?: (data?: T) => void) {
    setBusy(key);
    setError(null);
    setNote(null);
    const r = await action();
    setBusy(null);
    if (!r.ok) {
      setError({ message: r.message, code: r.code });
      return false;
    }
    done?.(r.data);
    return true;
  }

  function save(nextTags = tags) {
    return run("save", () =>
      onSave({
        body,
        thread_parts: thread ? parts : null,
        hashtags: nextTags,
        first_comment: firstComment.trim() || null,
      }),
    );
  }

  function addTag(raw: string) {
    const t = normalizeHashtag(raw);
    if (!t || tags.includes(t)) return;
    const next = [...tags, t];
    setTags(next);
    setNewTag("");
    void save(next);
  }

  function removeTag(t: string) {
    const next = tags.filter((x) => x !== t);
    setTags(next);
    void save(next);
  }

  function submitTag(e: FormEvent) {
    e.preventDefault();
    addTag(newTag);
  }

  const voiceItems = applicableVoices(voices ?? [], variant);
  const counted = (text: string) => weightedLength(text, variant.platform);

  return (
    <>
      <Panel aria-labelledby={`${id}-name`}>
        <PanelHead className="flex-wrap">
          <AccountChip
            id={`${id}-name`}
            size="lg"
            account={{ platform: platformIcon(variant.platform), name: variant.nickname, handle: variant.handle, avatar_url: variant.avatar_url, platformLabel: label }}
            meta={variant.group_name ? `via ${variant.group_name}` : undefined}
          />
          <span className="flex-1" />
          <span className="text-caption text-fg-subtle">Voice:</span>
          <Tag tone="outline" className="font-sans">
            {variant.voice_name}
            {sourceLabel ? ` · ${sourceLabel}` : ""}
          </Tag>
          {canEdit && !locked ? (
            <Button size="sm" aria-expanded={voiceOpen} onClick={() => setVoiceOpen((o) => !o)} disabled={!!busy}>
              Switch
            </Button>
          ) : null}
          <span className="flex items-center gap-2">
            <Switch
              checked={variant.enabled}
              // In approval or decided: switching an account on or off would change what is
              // being approved (the API answers variant_locked); edit the text instead.
              disabled={!canEdit || locked || !!busy || generating || !TOGGLEABLE.includes(variant.state)}
              aria-label={`Include ${variant.nickname} in this post`}
              onCheckedChange={(on) => void run("toggle", () => onToggle(on))}
            />
            <span aria-hidden="true" className="text-caption text-fg-muted">
              {variant.enabled ? "On" : "Off"}
            </span>
          </span>
        </PanelHead>

        <PanelBody>
          {voiceOpen && canEdit && !locked ? (
            <div className="mb-3.5 grid gap-2 rounded-md border border-border bg-surface-muted p-3">
              <Label htmlFor={`${id}-voice`}>Voice for {variant.nickname} in this post</Label>
              <Select id={`${id}-voice`} value={voiceChoice} onChange={(e) => setVoiceChoice(e.target.value)} disabled={!!busy}>
                <option value="">Automatic — account, then group, then workspace</option>
                {voiceItems.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} ({SCOPE_LABEL[v.scope_type]}
                    {v.scope_type === "workspace" ? "" : `: ${v.scope_name}`})
                  </option>
                ))}
              </Select>
              {voices === null ? <p className="text-caption text-fg-subtle">Couldn&apos;t load the voices. Reload to try again.</p> : null}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!!busy || voiceChoice === (variant.brand_voice_id ?? "")}
                  onClick={() => void run("voice", () => onVoice(voiceChoice || null), () => setVoiceOpen(false))}
                >
                  {busy === "voice" ? "Saving…" : "Use this voice"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setVoiceOpen(false)} disabled={!!busy}>
                  Cancel
                </Button>
                <span className="text-caption text-fg-subtle">Applies to the next draft or rewrite; the current text stays.</span>
              </div>
            </div>
          ) : null}

          {error ? (
            <Notice
              tone="danger"
              role="alert"
              className="mb-3.5"
              actions={
                error.code === "version_conflict" ? (
                  <Button size="sm" onClick={() => window.location.reload()}>
                    Reload
                  </Button>
                ) : undefined
              }
            >
              {error.message}
            </Notice>
          ) : null}
          <div role="status" aria-live="polite" className="empty:hidden">
            {note ? (
              <Notice tone="info" icon="check" className="mb-3.5">
                {note}
              </Notice>
            ) : null}
          </div>

          {!variant.enabled ? (
            <Notice tone="default" title="Off for this post." className="mb-3.5">
              {variant.nickname} drops out of the set without losing its history. Turn it back on to draft, approve and publish it
              with the others.
            </Notice>
          ) : null}

          {!version ? (
            generating && variant.enabled ? (
              <div aria-busy="true" className="grid gap-2 py-2">
                <span className="text-sm text-fg-muted">Drafting {variant.nickname}…</span>
                <Skeleton className="w-[86%]" />
                <Skeleton className="w-[72%]" />
                <Skeleton className="w-[58%]" />
              </div>
            ) : (
              <EmptyState
                icon="compose"
                tone="quiet"
                title="Not drafted yet"
                description={
                  variant.enabled
                    ? `Nothing has been written for ${variant.nickname} yet.`
                    : "It is off for this post, so nothing will be written for it."
                }
                action={
                  canEdit && variant.enabled ? (
                    <Button size="sm" variant="primary" disabled={!!busy || generating} onClick={() => void run("draft", onDraft)}>
                      {busy === "draft" ? "Queuing…" : `Draft ${variant.nickname}`}
                    </Button>
                  ) : undefined
                }
              />
            )
          ) : (
            <>
              {reasons.length > 0 ? (
                <Notice
                  tone={reasons.some((r) => r.outcome === "block") ? "danger" : "warn"}
                  title={reasons.some((r) => r.outcome === "block") ? "Blocked." : "Check before approval."}
                  className="mb-3.5"
                >
                  <ul className="mt-1 grid gap-1">
                    {reasons.map((r) => (
                      <li key={r.key}>{r.text}</li>
                    ))}
                  </ul>
                </Notice>
              ) : null}

              {locked ? (
                <p className="mb-2 text-caption text-fg-subtle">
                  This account&apos;s post is scheduled or published, so its text, hashtags and voice are fixed here.
                </p>
              ) : null}
              {generating ? (
                <p className="mb-2 text-caption text-fg-subtle">Drafting is running for this post — editing unlocks when it finishes.</p>
              ) : null}

              {thread ? (
                <div className="grid gap-2.5">
                  {parts.map((p, i) => (
                    <div key={i} className="grid gap-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor={`${id}-part-${i}`}>
                          Part {i + 1} of {parts.length}
                        </Label>
                        <CharCounter
                          count={counted(i === parts.length - 1 ? composeText(p, tags) : p)}
                          limit={limit}
                          className="text-fg-subtle"
                        />
                      </div>
                      <Textarea
                        id={`${id}-part-${i}`}
                        value={p}
                        maxLength={MAX_THREAD_PART_CHARS}
                        readOnly={!editable}
                        className="min-h-24"
                        onChange={(e) => setParts((ps) => ps.map((x, j) => (j === i ? e.target.value : x)))}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <>
                  <label htmlFor={`${id}-body`} className="sr-only">
                    Post text for {variant.nickname}
                  </label>
                  <Textarea
                    id={`${id}-body`}
                    value={body}
                    readOnly={!editable}
                    className="min-h-[190px]"
                    onChange={(e) => setBody(e.target.value)}
                  />
                </>
              )}

              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                {canEdit && !locked ? (
                  <>
                    {REWRITE_CHIPS.map((c) => (
                      <Button
                        key={c.label}
                        size="sm"
                        disabled={!editable || !!busy || dirty}
                        onClick={() => void run(`rewrite:${c.label}`, () => onRewrite(c.instruction), () => setNote(`Rewriting: ${c.label.toLowerCase()}…`))}
                      >
                        {c.label}
                      </Button>
                    ))}
                    <Button
                      size="sm"
                      disabled={!editable || !!busy || dirty}
                      onClick={() => void run("regenerate", onRegenerate, () => setNote(`Writing a fresh draft for ${variant.nickname}…`))}
                    >
                      <Icon name="refresh" size="sm" />
                      Regenerate
                    </Button>
                  </>
                ) : null}
                <span className="flex-1" />
                {!thread ? <CharCounter count={counted(composeText(body, tags))} limit={limit} className="text-fg-subtle" /> : null}
              </div>
              {dirty && canEdit ? (
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <Button size="sm" variant="primary" disabled={!editable || !!busy} onClick={() => void save()}>
                    {busy === "save" ? "Saving…" : "Save"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!!busy}
                    onClick={() => {
                      setBody(version.body);
                      setParts(thread ? [...thread] : []);
                      setTags(version.hashtags);
                      setFirstComment(version.first_comment ?? "");
                    }}
                  >
                    Discard changes
                  </Button>
                  <span className="text-caption text-fg-subtle">
                    Saving makes version {version.version_no + 1}; every earlier version stays in history. Rewrites wait until you save
                    or discard.
                  </span>
                </div>
              ) : (
                <p className="mt-2 text-caption text-fg-subtle">
                  Version {version.version_no} · {version.source === "human" ? "edited by a person" : "written by the agent"}
                  {version.revision_instruction ? ` · “${version.revision_instruction}”` : ""}
                </p>
              )}

              <Divider />

              <div className="grid gap-1.5">
                <Label htmlFor={`${id}-first-comment`}>First comment (optional)</Label>
                <Textarea
                  id={`${id}-first-comment`}
                  value={firstComment}
                  readOnly={!editable}
                  maxLength={1250}
                  className="min-h-24"
                  onChange={(e) => setFirstComment(e.target.value)}
                />
                <p className="text-caption text-fg-subtle">
                  Published only after the main post succeeds · {firstComment.length.toLocaleString()}/1,250
                </p>
              </div>

              <Divider />

              <HashtagSection
                tags={tags}
                suggestions={suggestions}
                canEdit={editable}
                busy={!!busy}
                suggesting={busy === "suggest"}
                newTag={newTag}
                onNewTag={setNewTag}
                onSubmitTag={submitTag}
                onAdd={addTag}
                onRemove={removeTag}
                onSuggest={() => void run("suggest", onSuggest, (data) => setSuggestions(data ?? []))}
                platformHashtags={conventionLine(variant.platform, limit).split(" · ")[1] ?? ""}
                nickname={variant.nickname}
              />

              <Divider />

              <MediaPanel
                workspaceId={workspaceId}
                platform={variant.platform}
                media={version.media}
                contentItemId={contentItemId}
                variantId={variant.id}
                editable={editable}
                onSave={onMedia}
              />

              <Divider />

              <AgentNotesSection notes={notes} issues={issues} validation={validation} confidence={version.confidence_score} />
            </>
          )}
        </PanelBody>
      </Panel>

      {version ? (
        <Panel aria-labelledby={`${id}-preview`}>
          <PanelHead>
            <h2 id={`${id}-preview`} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
              Preview
            </h2>
            <span className="flex-1" />
            <SegmentedControl<"desktop" | "mobile">
              aria-label="Preview size"
              options={[
                { value: "desktop", label: "Desktop" },
                { value: "mobile", label: "Mobile" },
              ]}
              value={viewport}
              onValueChange={setViewport}
            />
          </PanelHead>
          <PanelBody>
            <PlatformPreview
              platform={platformIcon(variant.platform)}
              viewport={viewport}
              author={{ name: variant.nickname, subtitle: `${variant.handle} · Now` }}
              body={body}
              threadParts={thread ? parts : null}
              hashtags={tags}
              media={previewMedia(workspaceId, version.media)}
              foot={<span>Renders as {label} shows it · {conventionLine(variant.platform, limit)}</span>}
            />
          </PanelBody>
        </Panel>
      ) : null}
    </>
  );
}

function HashtagSection({
  tags,
  suggestions,
  canEdit,
  busy,
  suggesting,
  newTag,
  onNewTag,
  onSubmitTag,
  onAdd,
  onRemove,
  onSuggest,
  platformHashtags,
  nickname,
}: {
  tags: string[];
  suggestions: HashtagSuggestion[] | null;
  canEdit: boolean;
  busy: boolean;
  suggesting: boolean;
  newTag: string;
  onNewTag: (v: string) => void;
  onSubmitTag: (e: FormEvent) => void;
  onAdd: (tag: string) => void;
  onRemove: (tag: string) => void;
  onSuggest: () => void;
  platformHashtags: string;
  nickname: string;
}) {
  const id = useId();
  const measured = suggestions?.some((s) => formatLift(s) !== null) ?? false;
  return (
    <section aria-labelledby={`${id}-h`}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 id={`${id}-h`} className="text-sm font-semibold">
          Hashtags
        </h3>
        {canEdit ? (
          <Button size="sm" onClick={onSuggest} disabled={busy}>
            <Icon name="hash" size="sm" />
            {suggesting ? "Suggesting…" : "Suggest"}
          </Button>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {tags.length === 0 ? <span className="text-caption text-fg-subtle">No hashtags. {platformHashtags} here.</span> : null}
        {tags.map((t) => (
          <Tag key={t} tone="info">
            #{t}
            {canEdit ? (
              <button
                type="button"
                className="text-fg-subtle hover:text-fg"
                aria-label={`Remove #${t}`}
                disabled={busy}
                onClick={() => onRemove(t)}
              >
                ×
              </button>
            ) : null}
          </Tag>
        ))}
        {canEdit ? (
          <form onSubmit={onSubmitTag} className="flex items-center gap-1.5" aria-label={`Add a hashtag for ${nickname}`}>
            <Input
              value={newTag}
              onChange={(e) => onNewTag(e.target.value)}
              aria-label="New hashtag"
              placeholder="+ add"
              className="h-6 min-h-6 w-28 py-0 text-xs"
              disabled={busy}
            />
            <Button type="submit" size="sm" variant="ghost" disabled={busy || !newTag.trim()}>
              Add
            </Button>
          </form>
        ) : null}
      </div>
      {suggestions !== null ? (
        suggestions.length === 0 ? (
          <p className="mt-2.5 text-caption text-fg-subtle">No suggestions for this text.</p>
        ) : (
          <table className="mt-2.5 w-full border-collapse overflow-hidden rounded-md border border-border text-sm">
            <caption className="sr-only">Hashtag suggestions for {nickname}, with their evidence</caption>
            <thead>
              <tr className="border-b border-border text-left text-caption text-fg-muted">
                <th scope="col" className="px-2.5 py-1.5 font-semibold">
                  Tag
                </th>
                <th scope="col" className="px-2.5 py-1.5 font-semibold">
                  Basis
                </th>
                <th scope="col" className="px-2.5 py-1.5 text-right font-semibold">
                  Lift vs median
                </th>
                <th scope="col" className="px-2.5 py-1.5 text-right font-semibold">
                  Posts
                </th>
                {canEdit ? (
                  <th scope="col" className="px-2.5 py-1.5">
                    <span className="sr-only">Add</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {suggestions.map((s) => {
                const lift = formatLift(s);
                const tag = normalizeHashtag(s.tag) ?? s.tag;
                return (
                  <tr key={s.tag} className="border-b border-border last:border-b-0" title={s.rationale || undefined}>
                    <td className="px-2.5 py-1.5 font-mono text-xs">#{tag}</td>
                    <td className="px-2.5 py-1.5">
                      <Tag tone={s.basis === "history" ? "ok" : "outline"} className={cn(s.basis !== "history" && "font-sans")}>
                        {basisLabel(s.basis)}
                      </Tag>
                    </td>
                    <td className="tabular px-2.5 py-1.5 text-right font-mono text-xs">
                      {lift ?? <Dash sr={s.sample_size < MIN_POSTS_FOR_LIFT ? "Not enough posts to measure" : "Not measured"} />}
                    </td>
                    <td className="tabular px-2.5 py-1.5 text-right font-mono text-xs">{s.sample_size}</td>
                    {canEdit ? (
                      <td className="px-2.5 py-1 text-right">
                        {tags.includes(tag) ? (
                          <span className="text-caption text-fg-subtle">Added</span>
                        ) : (
                          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAdd(tag)} aria-label={`Add #${tag}`}>
                            Add
                          </Button>
                        )}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )
      ) : null}
      {suggestions !== null && suggestions.length > 0 ? (
        <p className="mt-2 text-caption text-fg-subtle">
          Lift is against this account&apos;s own median, not the workspace&apos;s. Below {MIN_POSTS_FOR_LIFT} posts there is no usable
          signal, and the table says so rather than guessing.
          {measured ? "" : " This account has no measured history yet, so every suggestion comes from the content or the platform's convention."}
        </p>
      ) : null}
    </section>
  );
}

function AgentNotesSection({
  notes,
  issues,
  validation,
  confidence,
}: {
  notes: ReturnType<typeof readNotes>;
  issues: ReturnType<typeof readIssues>;
  validation: ReturnType<typeof readValidation>;
  confidence: number | null;
}) {
  const id = useId();
  const items: DescriptionItem[] = [];
  if (confidence !== null)
    items.push({
      term: "Editor confidence",
      detail: (
        <Tag tone={confidenceTone(confidence)}>
          {confidence.toFixed(2)}
        </Tag>
      ),
    });
  if (notes.angle) items.push({ term: "Angle", detail: notes.angle });
  if (notes.hookType) items.push({ term: "Hook", detail: HOOK_TYPE_LABEL[notes.hookType] ?? notes.hookType });
  if (notes.voiceName)
    items.push({
      term: "Voice",
      detail: `${notes.voiceName}${notes.voiceSource ? ` · ${voiceSourceLabel(notes.voiceSource)}` : ""}${
        notes.voiceMatch !== undefined ? ` · match ${notes.voiceMatch.toFixed(2)}` : ""
      }`,
    });
  if (notes.closestSiblingSimilarity !== undefined)
    items.push({ term: "Closest sibling", detail: `similarity ${notes.closestSiblingSimilarity.toFixed(2)}` });
  if (notes.differentiateAttempts !== undefined && notes.differentiateAttempts > 0)
    items.push({ term: "Rewritten to differ", detail: `${notes.differentiateAttempts}×` });

  const empty = items.length === 0 && !notes.selfCritique && issues.length === 0 && validation.length === 0;
  return (
    <section aria-labelledby={`${id}-h`}>
      <h3 id={`${id}-h`} className="mb-2 text-sm font-semibold">
        Why this draft
      </h3>
      {empty ? (
        <p className="text-caption text-fg-subtle">No agent notes on this version — it was written or edited by a person.</p>
      ) : (
        <div className="grid gap-2.5">
          {items.length ? <DescriptionList items={items} /> : null}
          {notes.selfCritique ? (
            <p className="text-sm text-fg-muted">
              <span className="font-semibold text-fg">Writer&apos;s self-critique: </span>
              {notes.selfCritique}
            </p>
          ) : null}
          {notes.confidenceRationale ? <p className="text-caption text-fg-subtle">{notes.confidenceRationale}</p> : null}
          {issues.length > 0 ? (
            <div>
              <h4 className="mb-1.5 text-caption font-semibold">Editor issues</h4>
              <ul className="grid gap-2">
                {issues.map((issue, i) => (
                  <li key={i} className="grid gap-0.5 rounded-md border border-border px-3 py-2">
                    <span className="flex flex-wrap items-center gap-2">
                      <Tag tone={issue.severity === "block" ? "danger" : issue.severity === "warning" ? "warn" : "neutral"}>
                        {issue.severity === "block" ? "Block" : issue.severity === "warning" ? "Warning" : "Note"}
                      </Tag>
                      <span className="text-sm font-medium">{issueCategoryLabel(issue.category)}</span>
                    </span>
                    {issue.excerpt ? <q className="font-mono text-xs text-fg-muted">{issue.excerpt}</q> : null}
                    {issue.explanation ? <span className="text-sm text-fg-muted">{issue.explanation}</span> : null}
                    {issue.suggestedFix ? (
                      <span className="text-sm">
                        <span className="font-semibold">Suggested fix: </span>
                        {issue.suggestedFix}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {validation.length > 0 ? (
            <div>
              <h4 className="mb-1.5 text-caption font-semibold">Platform checks</h4>
              <ul className="grid gap-1">
                {validation.map((v, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm">
                    <Tag tone={v.severity === "block" ? "danger" : "warn"}>{v.severity === "block" ? "Block" : "Warning"}</Tag>
                    {v.message}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function previewMedia(workspaceId: string, media: readonly Record<string, unknown>[]): PreviewMedia | null {
  const items = readMedia(media);
  const first = items[0];
  if (!first) return null;
  const size = first.width && first.height ? `${first.width} × ${first.height}` : (first.aspect ?? "image");
  return {
    label: items.length > 1 ? `${size} · 1 of ${items.length}` : size,
    summary: items.length > 1 ? `${items.length} images` : size,
    src: first.assetId ? mediaContentUrl(workspaceId, first.assetId) : undefined,
    alt: first.altText ?? "",
    ratio: first.width && first.height ? `${first.width} / ${first.height}` : undefined,
  };
}
