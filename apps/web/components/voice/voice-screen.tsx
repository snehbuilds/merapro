"use client";

import { createVoice, deleteVoice, sampleVoice, updateVoice, type AccountOut, type GroupOut, type Platform } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import { PlatformPreview } from "@/components/mera/platform-preview";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { DescriptionList } from "@/components/ui/description-list";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { PLATFORM_CATALOGUE, platformIcon } from "@/lib/accounts";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { displayMessage, type ApiError } from "@/lib/api/errors";
import type { Voice, VoiceSample } from "@/lib/api/models";
import { SCREENS } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { SCOPE_LABEL, SCOPE_SECTION, VOICE_ERROR_TEXT, appliesTo, groupVoicesByScope, voiceSummary } from "@/lib/voices";
import { SkillsPanel } from "./skills-panel";
import { VoiceForm } from "./voice-form";

/*
 * Brand Voice (brand-voice.html): every profile by scope (workspace, groups,
 * accounts; resolution is account, then group, then workspace), the
 * selected profile's editor, what it has learned from approvers' edits
 * (M6: empty until then) and a sample drafted with it. Editors and up
 * change voices; a scoped editor only at their own groups and accounts,
 * which the API enforces.
 */

export interface VoiceScreenProps {
  workspace: { id: string; slug: string; name: string; timezone: string; archived: boolean };
  voices: Voice[];
  groups: GroupOut[];
  accounts: AccountOut[];
  /** Editor+ (CONFIGURE_CONTENT). */
  canManage: boolean;
}

type Flash = { tone: "info" | "danger"; text: string };

function errorText(error: ApiError): string {
  return VOICE_ERROR_TEXT[error.code] ?? displayMessage(error);
}

/** Keep a local copy of the list so mutations show at once; a server refresh replaces it. */
function useVoiceList(fromServer: Voice[]) {
  const [state, setState] = useState({ fromServer, list: fromServer });
  if (state.fromServer !== fromServer) setState({ fromServer, list: fromServer });
  const setList = (update: (list: Voice[]) => Voice[]) => setState((s) => ({ ...s, list: update(s.list) }));
  return [state.list, setList] as const;
}

const SAMPLE_PLATFORMS = PLATFORM_CATALOGUE.filter((p) => p.phase === 1);

export function VoiceScreen({ workspace, voices: serverVoices, groups, accounts, canManage }: VoiceScreenProps) {
  const router = useRouter();
  const canEdit = canManage && !workspace.archived;
  const [voices, setVoices] = useVoiceList(serverVoices);
  const [selectedId, setSelectedId] = useState<string | "new" | undefined>(() =>
    serverVoices.length ? (serverVoices.find((v) => v.scope_type === "workspace" && v.is_default) ?? serverVoices[0])?.id : canEdit ? "new" : undefined,
  );
  const [flash, setFlash] = useState<Flash | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [formPending, setFormPending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const id = useId();
  const formId = `${id}-form`;
  const grouped = groupVoicesByScope(voices);
  const selected = selectedId && selectedId !== "new" ? voices.find((v) => v.id === selectedId) : undefined;
  const creating = selectedId === "new";

  function select(next: string | "new") {
    setSelectedId(next);
    setConfirmDelete(false);
    setFlash(null);
  }

  function upsert(v: Voice) {
    setVoices((list) => {
      const others = list.filter((x) => x.id !== v.id);
      // A new default clears the old one at the same scope (the API does the same).
      const cleared = v.is_default
        ? others.map((x) => (x.scope_type === v.scope_type && x.scope_id === v.scope_id && x.is_default ? { ...x, is_default: false } : x))
        : others;
      return [...cleared, v];
    });
  }

  async function duplicate(v: Voice) {
    setBusy("duplicate");
    setFlash(null);
    const r = await settle(
      createVoice({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspace.id },
        body: {
          name: `${v.name} (copy)`.slice(0, 80),
          tone: v.tone,
          target_audience: v.target_audience,
          emoji_policy: v.emoji_policy,
          cta_style: v.cta_style,
          banned_words: v.banned_words,
          example_posts: v.example_posts,
          about: v.about,
          story_bank: v.story_bank,
          scope_type: v.scope_type,
          scope_id: v.scope_id,
          is_default: false,
        },
      }),
    );
    setBusy(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return;
    }
    upsert(r.data);
    setSelectedId(r.data.id);
    setFlash({ tone: "info", text: `Duplicated ${v.name} as ${r.data.name}.` });
    router.refresh();
  }

  async function makeDefault(v: Voice) {
    setBusy("default");
    setFlash(null);
    const r = await settle(updateVoice({ client: browserApi, path: { workspace_id: workspace.id, voice_id: v.id }, body: { is_default: true } }));
    setBusy(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return;
    }
    upsert(r.data);
    setFlash({
      tone: "info",
      text: `${r.data.name} is now the default for ${r.data.scope_type === "workspace" ? "the workspace" : r.data.scope_name}.`,
    });
    router.refresh();
  }

  async function remove(v: Voice) {
    setBusy("delete");
    setFlash(null);
    const r = await settle(deleteVoice({ client: browserApi, path: { workspace_id: workspace.id, voice_id: v.id } }));
    setBusy(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: errorText(r.error) });
      return;
    }
    const rest = voices.filter((x) => x.id !== v.id);
    setVoices(() => rest);
    setSelectedId(rest[0]?.id ?? (canEdit ? "new" : undefined));
    setConfirmDelete(false);
    setFlash({ tone: "info", text: `Deleted ${v.name}. Accounts that used it fall back to the next voice up.` });
    router.refresh();
  }

  const n = voices.length;

  return (
    <>
      <Topbar
        title={SCREENS.voice.title}
        sub={`${n} ${n === 1 ? "profile" : "profiles"}`}
        actionNode={
          canEdit ? (
            <Button variant="primary" className="shrink-0 max-narrow:w-8 max-narrow:px-0" onClick={() => select("new")}>
              <Icon name="plus" size="sm" />
              <span className="max-narrow:sr-only">New profile</span>
            </Button>
          ) : undefined
        }
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
            {flash.text}
          </Notice>
        ) : null}

        <SkillsPanel />

        <div className="grid grid-cols-[300px_minmax(0,1fr)] items-start gap-3.5 max-wide:grid-cols-[260px_minmax(0,1fr)] max-rail:grid-cols-1">
          <Panel aria-labelledby={`${id}-profiles`}>
            <PanelHead>
              <h2 id={`${id}-profiles`} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
                Profiles
              </h2>
            </PanelHead>
            {grouped.length === 0 ? (
              <EmptyState
                icon="voice"
                tone="quiet"
                title="No voices yet"
                description="Until there is one, the Writer drafts in a plain built-in voice."
              />
            ) : (
              grouped.map((g) => (
                <section key={g.scope} aria-labelledby={`${id}-${g.scope}`}>
                  <h3
                    id={`${id}-${g.scope}`}
                    className="border-b border-border bg-surface-muted px-3.5 py-1.5 font-label text-2xs font-semibold tracking-[0.09em] text-fg-muted uppercase"
                  >
                    {SCOPE_SECTION[g.scope]}
                  </h3>
                  <ul>
                    {g.voices.map((v) => (
                      <li key={v.id}>
                        <button
                          type="button"
                          aria-current={v.id === selectedId ? "true" : undefined}
                          onClick={() => select(v.id)}
                          className="grid w-full gap-[5px] border-b border-border px-3.5 py-3 text-left hover:bg-surface-hover aria-[current=true]:bg-accent-soft"
                        >
                          <span className="flex items-center gap-2">
                            <span className={cn("min-w-0 truncate text-sm font-medium", v.id === selectedId && "font-semibold")}>{v.name}</span>
                            <span className="flex-1" />
                            {v.is_default ? <Tag tone="info">Default</Tag> : null}
                            <Tag tone="outline" className="font-sans">
                              {SCOPE_LABEL[v.scope_type]}
                            </Tag>
                          </span>
                          <span className="truncate text-sm text-fg-muted">{voiceSummary(v)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))
            )}
            <PanelFoot>Resolution is account, then group, then workspace. Each scope can have one default.</PanelFoot>
          </Panel>

          <div className="grid min-w-0 gap-3.5">
            {creating && canEdit ? (
              <Panel aria-labelledby={`${id}-new`}>
                <PanelHead
                  actions={
                    <>
                      {voices.length ? (
                        <Button size="sm" variant="ghost" onClick={() => select(voices[0]!.id)} disabled={formPending}>
                          Cancel
                        </Button>
                      ) : null}
                      <Button size="sm" variant="primary" type="submit" form={formId} disabled={formPending}>
                        {formPending ? "Creating…" : "Create profile"}
                      </Button>
                    </>
                  }
                >
                  <h2 id={`${id}-new`} className="text-md font-semibold">
                    New profile
                  </h2>
                </PanelHead>
                <PanelBody>
                  <VoiceForm
                    workspaceId={workspace.id}
                    groups={groups}
                    accounts={accounts}
                    canEdit={canEdit}
                    formId={formId}
                    onPendingChange={setFormPending}
                    onSaved={(v) => {
                      upsert(v);
                      setSelectedId(v.id);
                      setFlash({ tone: "info", text: `Created ${v.name}.` });
                      router.refresh();
                    }}
                  />
                </PanelBody>
              </Panel>
            ) : selected ? (
              <>
                <Panel aria-labelledby={`${id}-voice`}>
                  <PanelHead
                    className="flex-wrap"
                    actions={
                      canEdit ? (
                        <>
                          {!selected.is_default ? (
                            <Button size="sm" onClick={() => void makeDefault(selected)} disabled={!!busy || formPending}>
                              {busy === "default" ? "Saving…" : "Set as default"}
                            </Button>
                          ) : null}
                          <Button size="sm" onClick={() => void duplicate(selected)} disabled={!!busy || formPending}>
                            {busy === "duplicate" ? "Duplicating…" : "Duplicate"}
                          </Button>
                          <Button size="sm" variant="danger" onClick={() => setConfirmDelete(true)} disabled={!!busy || formPending}>
                            Delete
                          </Button>
                          <Button size="sm" variant="primary" type="submit" form={formId} disabled={!!busy || formPending}>
                            {formPending ? "Saving…" : "Save"}
                          </Button>
                        </>
                      ) : undefined
                    }
                  >
                    <h2 id={`${id}-voice`} className="min-w-0 truncate text-md font-semibold">
                      {selected.name}
                    </h2>
                    <Tag tone="outline" className="font-sans">
                      {appliesTo(selected)}
                    </Tag>
                    {selected.is_default ? <Tag tone="info">Default</Tag> : null}
                  </PanelHead>
                  <PanelBody className="grid gap-3.5">
                    {confirmDelete ? (
                      <Notice
                        tone="danger"
                        title={`Delete ${selected.name}?`}
                        actions={
                          <>
                            <Button size="sm" variant="danger" onClick={() => void remove(selected)} disabled={busy === "delete"}>
                              {busy === "delete" ? "Deleting…" : "Delete voice"}
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)} disabled={busy === "delete"}>
                              Cancel
                            </Button>
                          </>
                        }
                      >
                        Drafts already written keep their text. New drafts use the next voice up ({selected.scope_type === "social_account" ? "the group's, then the workspace's" : selected.scope_type === "account_group" ? "the workspace's" : "the built-in voice"}).
                      </Notice>
                    ) : null}
                    {!canEdit ? (
                      <p className="text-caption text-fg-subtle">
                        {workspace.archived ? "This workspace is archived, so voices are read-only." : "Editors and up change voices."}
                      </p>
                    ) : null}
                    <VoiceForm
                      key={`${selected.id}:${selected.updated_at}`}
                      workspaceId={workspace.id}
                      voice={selected}
                      groups={groups}
                      accounts={accounts}
                      canEdit={canEdit}
                      formId={formId}
                      onPendingChange={setFormPending}
                      onSaved={(v, created) => {
                        upsert(v);
                        setFlash({ tone: "info", text: created ? `Created ${v.name}.` : `Saved ${v.name}.` });
                        router.refresh();
                      }}
                    />
                  </PanelBody>
                </Panel>

                <LearnedPanel voice={selected} />
                <SamplePanel key={selected.id} workspaceId={workspace.id} voice={selected} canEdit={canEdit} />
              </>
            ) : (
              <Panel>
                <EmptyState
                  icon="voice"
                  title="No voice selected"
                  description={canEdit ? "Pick a profile, or create one with New profile." : "Editors and up create profiles."}
                />
              </Panel>
            )}
          </div>
        </div>
      </main>
    </>
  );
}

function LearnedPanel({ voice }: { voice: Voice }) {
  const id = useId();
  const rows = voice.learned_preferences.map((raw) => {
    const r = raw as Record<string, unknown>;
    const text = [r.rule, r.correction, r.text, r.summary].find((x): x is string => typeof x === "string" && x.trim() !== "");
    const times = typeof r.count === "number" ? r.count : typeof r.times === "number" ? r.times : null;
    return { text: text ?? "A correction", times };
  });
  return (
    <Panel aria-labelledby={`${id}-learned`}>
      <PanelHead>
        <h2 id={`${id}-learned`} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
          Learned from your edits
        </h2>
        <span className="flex-1" />
        <span className="text-caption text-fg-subtle">{voice.scope_type === "social_account" ? "This account only" : `This ${SCOPE_LABEL[voice.scope_type].toLowerCase()} only`}</span>
      </PanelHead>
      {rows.length === 0 ? (
        <EmptyState
          icon="approvals"
          tone="quiet"
          title="Nothing learned yet"
          description="Edits approvers make attach to the account's voice and show here. That arrives with the Approval Queue (M6)."
        />
      ) : (
        <ul>
          {rows.map((r, i) => (
            <li key={i} className="flex items-center gap-3 border-b border-border px-3.5 py-2.5 text-sm last:border-b-0">
              <span className="min-w-0 flex-1">{r.text}</span>
              {r.times !== null ? <span className="tabular font-mono text-xs text-fg-subtle">{r.times}×</span> : null}
            </li>
          ))}
        </ul>
      )}
      <PanelFoot>
        Edits you make when approving attach to the account&apos;s voice, not the workspace&apos;s — &ldquo;be less formal&rdquo; means
        something different on a company page.
      </PanelFoot>
    </Panel>
  );
}

function SamplePanel({ workspaceId, voice, canEdit }: { workspaceId: string; voice: Voice; canEdit: boolean }) {
  const [topic, setTopic] = useState("");
  const [platform, setPlatform] = useState<Platform>("linkedin_member");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [topicError, setTopicError] = useState<string | null>(null);
  const [sample, setSample] = useState<{ platform: Platform; value: VoiceSample } | null>(null);
  const id = useId();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setTopicError(null);
    if (!topic.trim()) {
      setTopicError("Say what the sample should be about.");
      return;
    }
    setPending(true);
    const r = await settle(
      sampleVoice({ client: browserApi, path: { workspace_id: workspaceId, voice_id: voice.id }, body: { topic: topic.trim(), platform } }),
    );
    setPending(false);
    if (!r.ok) {
      setError(errorText(r.error));
      return;
    }
    setSample({ platform, value: r.data });
  }

  return (
    <Panel aria-labelledby={`${id}-sample`}>
      <PanelHead>
        <h2 id={`${id}-sample`} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
          Test this voice
        </h2>
      </PanelHead>
      <PanelBody className="grid gap-3">
        {canEdit ? (
          <form onSubmit={submit} noValidate aria-label={`Test ${voice.name}`} className="grid gap-2.5">
            <div className="grid grid-cols-[minmax(0,1fr)_200px] gap-3 max-narrow:grid-cols-1">
              <Field label="Draft a sample about" error={topicError ?? undefined}>
                <Input value={topic} maxLength={500} disabled={pending} onChange={(e) => setTopic(e.target.value)} />
              </Field>
              <Field label="Platform">
                <Select value={platform} disabled={pending} onChange={(e) => setPlatform(e.target.value as Platform)}>
                  {SAMPLE_PLATFORMS.map((p) => (
                    <option key={p.platform} value={p.platform}>
                      {p.label}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-2.5">
              <Button type="submit" size="sm" variant="primary" disabled={pending}>
                {pending ? "Generating…" : "Generate sample"}
              </Button>
              <span className="text-caption text-fg-subtle">Runs the Writer with this profile and nothing else changed. Nothing is saved.</span>
            </div>
          </form>
        ) : (
          <p className="text-sm text-fg-muted">Editors and up can draft a sample with this voice.</p>
        )}
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        <div aria-live="polite" className="grid gap-2.5 empty:hidden">
          {sample ? (
            <>
              <PlatformPreview
                platform={platformIcon(sample.platform)}
                author={{ name: "Sample account", subtitle: `Voice: ${sample.value.voice_name}` }}
                body={sample.value.body}
              />
              <DescriptionList
                items={[
                  { term: "Angle", detail: sample.value.angle || "—" },
                  { term: "Self-critique", detail: sample.value.self_critique || "—" },
                ]}
              />
            </>
          ) : null}
        </div>
      </PanelBody>
    </Panel>
  );
}
