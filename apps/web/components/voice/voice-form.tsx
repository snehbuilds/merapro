"use client";

import { createVoice, updateVoice, type AccountOut, type GroupOut, type VoicePatch } from "@mera/sdk";
import { useId, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { formErrors } from "@/lib/api/errors";
import type { Voice, VoiceScopeType } from "@/lib/api/models";
import { joinKeywords, parseKeywords } from "@/lib/sources";
import {
  CTA_STYLES,
  EMOJI_POLICIES,
  MAX_BANNED_WORDS,
  MAX_EXAMPLES,
  MAX_STORIES,
  SCOPE_LABEL,
  VOICE_ERROR_TEXT,
  VOICE_FORM_FIELDS,
  type VoiceFormField,
} from "@/lib/voices";

/*
 * Account profile editor (was brand-voice.html): who is posting, tone,
 * audience, emoji policy, CTA, banned words (a hard guardrail), the story
 * bank and real past posts. The platform skills decide how a post is built;
 * this decides whose post it is. Creating also
 * picks the scope: the workspace, one group or one account. Read-only for
 * anyone below Editor.
 */

export interface VoiceFormProps {
  workspaceId: string;
  /** Absent: create a new voice. */
  voice?: Voice;
  groups: readonly Pick<GroupOut, "id" | "name">[];
  accounts: readonly Pick<AccountOut, "id" | "nickname" | "status">[];
  canEdit: boolean;
  /** The id of the form, so the panel head's Save button can submit it. */
  formId: string;
  onSaved: (voice: Voice, created: boolean) => void;
  onPendingChange?: (pending: boolean) => void;
}

export function VoiceForm({ workspaceId, voice, groups, accounts, canEdit, formId, onSaved, onPendingChange }: VoiceFormProps) {
  const [name, setName] = useState(voice?.name ?? "");
  const [tone, setTone] = useState(voice?.tone ?? "");
  const [audience, setAudience] = useState(voice?.target_audience ?? "");
  const [emoji, setEmoji] = useState(voice?.emoji_policy ?? "none");
  const [cta, setCta] = useState(voice?.cta_style ?? "");
  const [banned, setBanned] = useState(joinKeywords(voice?.banned_words ?? []));
  const [examples, setExamples] = useState<string[]>(voice?.example_posts ?? []);
  const [about, setAbout] = useState(voice?.about ?? "");
  const [stories, setStories] = useState<string[]>(voice?.story_bank ?? []);
  const [scopeType, setScopeType] = useState<VoiceScopeType>(voice?.scope_type ?? "workspace");
  const [scopeId, setScopeId] = useState(voice?.scope_id ?? "");
  const [isDefault, setIsDefault] = useState(false);
  const [pending, setPendingState] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<VoiceFormField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const id = useId();
  const disabled = !canEdit || pending;
  const creating = !voice;
  const ctaOptions = cta && !CTA_STYLES.some((c) => c.value === cta) ? [...CTA_STYLES, { value: cta, label: cta }] : CTA_STYLES;
  const liveAccounts = accounts.filter((a) => a.status !== "disconnected");

  function setPending(p: boolean) {
    setPendingState(p);
    onPendingChange?.(p);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!canEdit) return;
    setErrors({});
    setFormError(null);
    const words = parseKeywords(banned);
    const posts = examples.map((p) => p.trim()).filter(Boolean);
    const bank = stories.map((p) => p.trim()).filter(Boolean);
    const local: Partial<Record<VoiceFormField, string>> = {};
    if (!name.trim()) local.name = "Name the voice.";
    if (words.length > MAX_BANNED_WORDS) local.banned_words = `At most ${MAX_BANNED_WORDS} banned words.`;
    if (posts.length > MAX_EXAMPLES) local.example_posts = `At most ${MAX_EXAMPLES} example posts.`;
    if (bank.length > MAX_STORIES) local.story_bank = `At most ${MAX_STORIES} entries.`;
    if (creating && scopeType !== "workspace" && !scopeId) local.scope_id = scopeType === "account_group" ? "Pick a group." : "Pick an account.";
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    setPending(true);
    let r;
    if (voice) {
      const patch: VoicePatch = {};
      if (name.trim() !== voice.name) patch.name = name.trim();
      if (tone.trim() !== (voice.tone ?? "")) patch.tone = tone.trim();
      if (audience.trim() !== (voice.target_audience ?? "")) patch.target_audience = audience.trim();
      if (emoji !== voice.emoji_policy) patch.emoji_policy = emoji;
      if (cta !== (voice.cta_style ?? "")) patch.cta_style = cta;
      if (joinKeywords(words) !== joinKeywords(voice.banned_words)) patch.banned_words = words;
      if (posts.join("\u0000") !== voice.example_posts.join("\u0000")) patch.example_posts = posts;
      if (about.trim() !== (voice.about ?? "")) patch.about = about.trim();
      if (bank.join("\u0000") !== voice.story_bank.join("\u0000")) patch.story_bank = bank;
      if (Object.keys(patch).length === 0) {
        setPending(false);
        setFormError(null);
        onSaved(voice, false);
        return;
      }
      r = await settle(updateVoice({ client: browserApi, path: { workspace_id: workspaceId, voice_id: voice.id }, body: patch }));
    } else {
      r = await settle(
        createVoice({
          client: browserApi,
          headers: idempotencyHeaders(),
          path: { workspace_id: workspaceId },
          body: {
            name: name.trim(),
            tone: tone.trim() || null,
            target_audience: audience.trim() || null,
            emoji_policy: emoji,
            cta_style: cta || null,
            banned_words: words,
            example_posts: posts,
            about: about.trim() || null,
            story_bank: bank,
            scope_type: scopeType,
            scope_id: scopeType === "workspace" ? null : scopeId,
            is_default: isDefault,
          },
        }),
      );
    }
    setPending(false);
    if (!r.ok) {
      const split = formErrors(r.error, VOICE_FORM_FIELDS);
      setErrors(split.fields);
      if (split.form.length) setFormError(VOICE_ERROR_TEXT[r.error.code] ?? split.form.join(" "));
      return;
    }
    onSaved(r.data, creating);
  }

  return (
    <form id={formId} onSubmit={submit} noValidate aria-label={voice ? `Edit ${voice.name}` : "New profile"} className="grid gap-3.5">
      {formError ? (
        <Notice tone="danger" role="alert">
          {formError}
        </Notice>
      ) : null}
      <fieldset disabled={!canEdit} className="grid min-w-0 gap-3.5">
        <legend className="sr-only">{voice ? `${voice.name} settings` : "New profile settings"}</legend>
        <Field label="Name" error={errors.name}>
          <Input value={name} maxLength={80} disabled={disabled} onChange={(e) => setName(e.target.value)} />
        </Field>
        {creating ? (
          <div className="grid grid-cols-2 gap-3.5 max-narrow:grid-cols-1">
            <Field label="Scope" error={errors.scope_type} hint="Account beats group beats workspace when drafting.">
              <Select
                value={scopeType}
                disabled={disabled}
                onChange={(e) => {
                  setScopeType(e.target.value as VoiceScopeType);
                  setScopeId("");
                }}
              >
                {(["workspace", "account_group", "social_account"] as const).map((s) => (
                  <option key={s} value={s}>
                    {SCOPE_LABEL[s]}
                  </option>
                ))}
              </Select>
            </Field>
            {scopeType === "account_group" ? (
              <Field label="Group" error={errors.scope_id}>
                <Select value={scopeId} disabled={disabled} onChange={(e) => setScopeId(e.target.value)}>
                  <option value="">Pick a group…</option>
                  {groups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : scopeType === "social_account" ? (
              <Field label="Account" error={errors.scope_id}>
                <Select value={scopeId} disabled={disabled} onChange={(e) => setScopeId(e.target.value)}>
                  <option value="">Pick an account…</option>
                  {liveAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.nickname}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}
          </div>
        ) : null}
        <Field
          label="About (who is posting)"
          hint="The platform skills' voice profile: role, expertise, what this account is known for. The Writer never claims more than this."
          error={errors.about}
        >
          <Textarea value={about} maxLength={1000} rows={3} disabled={disabled} onChange={(e) => setAbout(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3.5 max-narrow:grid-cols-1">
          <Field label="Tone" error={errors.tone}>
            <Input value={tone} maxLength={1000} disabled={disabled} onChange={(e) => setTone(e.target.value)} />
          </Field>
          <Field label="Target audience" error={errors.target_audience}>
            <Input value={audience} maxLength={500} disabled={disabled} onChange={(e) => setAudience(e.target.value)} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3.5 max-narrow:grid-cols-1">
          <Field label="Emoji policy" error={errors.emoji_policy}>
            <Select value={emoji} disabled={disabled} onChange={(e) => setEmoji(e.target.value as typeof emoji)}>
              {EMOJI_POLICIES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="CTA style" error={errors.cta_style}>
            <Select value={cta} disabled={disabled} onChange={(e) => setCta(e.target.value)}>
              {ctaOptions.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field
          label="Banned words and phrases"
          hint="Comma separated. A hard guardrail — a draft containing one is blocked, including a draft you typed yourself."
          error={errors.banned_words}
        >
          <Input value={banned} disabled={disabled} onChange={(e) => setBanned(e.target.value)} />
        </Field>
        {creating ? (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--accent)]"
              checked={isDefault}
              disabled={disabled}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Make it the default for this scope
          </label>
        ) : null}

        <div className="h-px bg-border" />

        <section aria-labelledby={`${id}-stories`} className="grid gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id={`${id}-stories`} className="text-sm font-semibold">
              Story bank
            </h3>
            {canEdit ? (
              <Button size="sm" disabled={disabled || stories.length >= MAX_STORIES} onClick={() => setStories((x) => [...x, ""])}>
                <Icon name="plus" size="sm" />
                Add story
              </Button>
            ) : null}
          </div>
          {errors.story_bank ? <p className="text-caption font-medium text-danger">{errors.story_bank}</p> : null}
          {stories.length === 0 ? (
            <p className="text-caption text-fg-subtle">
              True stories, numbers and results this account can tell, up to {MAX_STORIES}. The skills never invent figures; a post
              uses a personal story only from here.
            </p>
          ) : (
            <ol className="grid gap-2">
              {stories.map((st, i) => (
                <li key={i} className="flex items-start gap-2">
                  <Textarea
                    value={st}
                    aria-label={`Story ${i + 1}`}
                    maxLength={600}
                    rows={2}
                    disabled={disabled}
                    className="min-h-12 flex-1 text-sm"
                    onChange={(e) => setStories((x) => x.map((p, j) => (j === i ? e.target.value : p)))}
                  />
                  {canEdit ? (
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Remove story ${i + 1}`}
                      disabled={disabled}
                      onClick={() => setStories((x) => x.filter((_, j) => j !== i))}
                    >
                      <Icon name="x" size="sm" />
                    </Button>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>

        <div className="h-px bg-border" />

        <section aria-labelledby={`${id}-examples`} className="grid gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 id={`${id}-examples`} className="text-sm font-semibold">
              Example posts
            </h3>
            {canEdit ? (
              <Button size="sm" disabled={disabled || examples.length >= MAX_EXAMPLES} onClick={() => setExamples((x) => [...x, ""])}>
                <Icon name="plus" size="sm" />
                Add example
              </Button>
            ) : null}
          </div>
          {errors.example_posts ? <p className="text-caption font-medium text-danger">{errors.example_posts}</p> : null}
          {examples.length === 0 ? (
            <p className="text-caption text-fg-subtle">
              No examples yet. Up to {MAX_EXAMPLES} posts that sound right; the Writer uses them as few-shot examples.
            </p>
          ) : (
            <ol className="grid gap-2">
              {examples.map((ex, i) => (
                <li key={i} className="overflow-hidden rounded-lg border border-border">
                  <Textarea
                    value={ex}
                    aria-label={`Example ${i + 1}`}
                    maxLength={3000}
                    rows={3}
                    disabled={disabled}
                    className="min-h-16 rounded-none border-0 text-sm"
                    onChange={(e) => setExamples((x) => x.map((p, j) => (j === i ? e.target.value : p)))}
                  />
                  <div className="flex items-center gap-2 border-t border-border px-3 py-1.5 text-xs text-fg-subtle">
                    <span>
                      Few-shot example · {i + 1} of {examples.length}
                    </span>
                    <span className="flex-1" />
                    {canEdit ? (
                      <Button
                        size="icon-sm"
                        variant="ghost"
                        aria-label={`Remove example ${i + 1}`}
                        disabled={disabled}
                        onClick={() => setExamples((x) => x.filter((_, j) => j !== i))}
                      >
                        <Icon name="x" size="sm" />
                      </Button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      </fieldset>
    </form>
  );
}
