"use client";

import { createPillar, deletePillar, updatePillar, type GroupOut, type PillarPatch } from "@mera/sdk";
import { useId, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { formErrors, type ApiError } from "@/lib/api/errors";
import type { Pillar } from "@/lib/api/models";
import { DISCOVERY_ERROR_TEXT, PILLAR_FORM_FIELDS, formatWeight, pillarScopeLabel } from "@/lib/discovery";
import { joinKeywords, parseKeywords } from "@/lib/sources";

/*
 * discovery.html "Pillars · <group>": the topics this group's items are
 * scored against (the group's own pillars plus the workspace-wide ones).
 * Editors add, edit and delete them; the API rescores at once, so the
 * trending list reloads after every change. Automatic weight tuning is the
 * Phase 3 analytics loop (docs/12 §4).
 */

type PillarField = (typeof PILLAR_FORM_FIELDS)[number];

export interface PillarsPanelProps {
  workspaceId: string;
  groups: readonly Pick<GroupOut, "id" | "name">[];
  /** The group the screen is ranked for; null = whole workspace. */
  groupId: string | null;
  /** null when the list couldn't be loaded. */
  pillars: Pillar[] | null;
  canEdit: boolean;
  /** After a create, update or delete: reload pillars and rankings. */
  onChanged: (message: string) => void;
}

function errorMessage(error: ApiError, form: string[]): string {
  return DISCOVERY_ERROR_TEXT[error.code] ?? form.join(" ");
}

export function PillarsPanel({ workspaceId, groups, groupId, pillars, canEdit, onChanged }: PillarsPanelProps) {
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const groupName = groupId ? (groups.find((g) => g.id === groupId)?.name ?? "Group") : "Whole workspace";

  async function remove(p: Pillar) {
    setPending(true);
    setError(null);
    const r = await settle(deletePillar({ client: browserApi, path: { workspace_id: workspaceId, pillar_id: p.id } }));
    setPending(false);
    if (!r.ok) {
      setError(errorMessage(r.error, formErrors(r.error, []).form));
      return;
    }
    setDeleting(null);
    onChanged(`Deleted pillar ${p.name}. Rankings updated.`);
  }

  return (
    <Panel aria-labelledby={titleId}>
      <PanelHead
        actions={
          canEdit && editing !== "new" ? (
            <Button size="sm" variant="ghost" onClick={() => setEditing("new")}>
              <Icon name="plus" size="sm" />
              Add pillar
            </Button>
          ) : undefined
        }
      >
        <h2 id={titleId} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
          Pillars · {groupName}
        </h2>
      </PanelHead>
      <PanelBody className="grid gap-2.5">
        {error ? (
          <p role="alert" className="text-caption text-danger">
            {error}
          </p>
        ) : null}
        {editing === "new" ? (
          <PillarForm
            workspaceId={workspaceId}
            groups={groups}
            defaultGroupId={groupId}
            onCancel={() => setEditing(null)}
            onDone={(message) => {
              setEditing(null);
              onChanged(message);
            }}
          />
        ) : null}
        {pillars === null ? (
          <p className="text-sm text-fg-muted">Couldn&apos;t load pillars. Reload to try again.</p>
        ) : pillars.length === 0 ? (
          <p className="text-sm text-fg-muted">
            No pillars yet. Relevance is similarity to pillars, so nothing is scored for {groupId ? "this group" : "the workspace"} until
            one exists.
          </p>
        ) : (
          <ul className="grid gap-1.5">
            {pillars.map((p) =>
              editing === p.id ? (
                <li key={p.id}>
                  <PillarForm
                    workspaceId={workspaceId}
                    groups={groups}
                    defaultGroupId={groupId}
                    pillar={p}
                    onCancel={() => setEditing(null)}
                    onDone={(message) => {
                      setEditing(null);
                      onChanged(message);
                    }}
                  />
                </li>
              ) : (
                <li key={p.id} className="grid gap-1">
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 truncate text-sm">{p.name}</span>
                    {p.account_group_id === null || !groupId ? (
                      <Tag tone="neutral">{pillarScopeLabel(p, groups)}</Tag>
                    ) : null}
                    <span className="flex-1" />
                    <span className="tabular font-mono text-mono-xs">{formatWeight(p.weight)}</span>
                    {canEdit ? (
                      <>
                        <Button size="icon-sm" variant="ghost" aria-label={`Edit pillar ${p.name}`} onClick={() => setEditing(p.id)}>
                          <Icon name="edit" size="sm" />
                        </Button>
                        <Button size="icon-sm" variant="ghost" aria-label={`Delete pillar ${p.name}`} onClick={() => setDeleting(p.id)}>
                          <Icon name="trash" size="sm" />
                        </Button>
                      </>
                    ) : null}
                  </div>
                  {!p.embedded ? (
                    <span className="text-caption text-fg-subtle">Not scoring yet — its embedding is still being made.</span>
                  ) : null}
                  {deleting === p.id ? (
                    <Notice
                      tone="danger"
                      title={`Delete ${p.name}?`}
                      actions={
                        <>
                          <Button size="sm" variant="danger" onClick={() => remove(p)} disabled={pending}>
                            {pending ? "Deleting…" : "Delete pillar"}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setDeleting(null)} disabled={pending}>
                            Cancel
                          </Button>
                        </>
                      }
                    >
                      Items are rescored without it.
                    </Notice>
                  ) : null}
                </li>
              ),
            )}
          </ul>
        )}
        <p className="text-caption text-fg-subtle">
          Weights are yours to set for now. The analytics feedback loop will tune them later, bounded to 15% movement per run.
        </p>
      </PanelBody>
    </Panel>
  );
}

function PillarForm({
  workspaceId,
  groups,
  defaultGroupId,
  pillar,
  onCancel,
  onDone,
}: {
  workspaceId: string;
  groups: readonly Pick<GroupOut, "id" | "name">[];
  defaultGroupId: string | null;
  pillar?: Pillar;
  onCancel: () => void;
  onDone: (message: string) => void;
}) {
  const [name, setName] = useState(pillar?.name ?? "");
  const [description, setDescription] = useState(pillar?.description ?? "");
  const [keywords, setKeywords] = useState(joinKeywords(pillar?.keywords ?? []));
  const [weight, setWeight] = useState(String(pillar?.weight ?? 1));
  const [scope, setScope] = useState(defaultGroupId ?? "");
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<PillarField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setFormError(null);
    const kws = parseKeywords(keywords);
    const w = Number(weight);
    const local: Partial<Record<PillarField, string>> = {};
    if (!name.trim()) local.name = "Give the pillar a name.";
    if (kws.length > 20) local.keywords = "At most 20 keywords.";
    if (weight.trim() === "" || !Number.isFinite(w) || w < 0 || w > 4) local.weight = "Weight must be between 0 and 4.";
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    setPending(true);
    let r;
    if (pillar) {
      const patch: PillarPatch = {};
      if (name.trim() !== pillar.name) patch.name = name.trim();
      if (description.trim() !== (pillar.description ?? "")) patch.description = description.trim();
      if (joinKeywords(kws) !== joinKeywords(pillar.keywords)) patch.keywords = kws;
      if (w !== pillar.weight) patch.weight = w;
      if (Object.keys(patch).length === 0) {
        setPending(false);
        onCancel();
        return;
      }
      r = await settle(updatePillar({ client: browserApi, path: { workspace_id: workspaceId, pillar_id: pillar.id }, body: patch }));
    } else {
      r = await settle(
        createPillar({
          client: browserApi,
          headers: idempotencyHeaders(),
          path: { workspace_id: workspaceId },
          body: { name: name.trim(), description: description.trim() || null, keywords: kws, weight: w, group_id: scope || null },
        }),
      );
    }
    setPending(false);
    if (!r.ok) {
      const split = formErrors(r.error, PILLAR_FORM_FIELDS);
      setErrors(split.fields);
      if (split.form.length) setFormError(errorMessage(r.error, split.form));
      return;
    }
    onDone(pillar ? `Saved pillar ${r.data.name}. Rankings updated.` : `Added pillar ${r.data.name}. Rankings updated.`);
  }

  return (
    <form
      onSubmit={submit}
      noValidate
      aria-label={pillar ? `Edit pillar ${pillar.name}` : "Add a pillar"}
      className="grid gap-2.5 rounded-md border border-border bg-surface-muted p-3"
    >
      {formError ? (
        <Notice tone="danger" role="alert">
          {formError}
        </Notice>
      ) : null}
      <Field label="Pillar name" error={errors.name}>
        <Input value={name} maxLength={80} disabled={pending} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Description" error={errors.description} hint="What belongs here. Up to 500 characters.">
        <Textarea
          value={description}
          maxLength={500}
          rows={2}
          className="min-h-14"
          disabled={pending}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      <Field label="Keywords" error={errors.keywords} hint="Comma separated, up to 20.">
        <Input value={keywords} disabled={pending} onChange={(e) => setKeywords(e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-2.5">
        <Field label="Weight" error={errors.weight} hint="0 to 4.">
          <Input inputMode="decimal" value={weight} disabled={pending} onChange={(e) => setWeight(e.target.value)} />
        </Field>
        {pillar ? null : (
          <Field label="Applies to" error={errors.group_id}>
            <Select value={scope} disabled={pending} onChange={(e) => setScope(e.target.value)}>
              <option value="">Whole workspace</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={pending}>
          {pending ? "Saving…" : pillar ? "Save pillar" : "Add pillar"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
