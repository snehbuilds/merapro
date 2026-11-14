"use client";

import {
  archiveWorkspace,
  deleteWorkspace,
  unarchiveWorkspace,
  updateWorkspace,
  type LevelSettings,
  type WorkspacePatch,
  type WorkspaceType,
} from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage, formErrors } from "@/lib/api/errors";
import { HHMM, parseBoundedInt } from "@/lib/level-settings";

/*
 * settings.html: the workspace profile, its default settings (the
 * "workspace" level of docs/03 §2.2), and the danger zone. Admin+ edits the
 * profile and defaults and archives; only an owner deletes.
 */

export interface WorkspaceSettingsView {
  id: string;
  name: string;
  slug: string;
  timezone: string;
  workspace_type: string;
  archived: boolean;
}

export const WORKSPACE_TYPE_LABEL: Readonly<Record<WorkspaceType, string>> = {
  personal: "Personal",
  team: "Team",
  agency: "Agency client",
};

const WORKSPACE_ERRORS: Readonly<Record<string, string>> = {
  workspace_archived: "This workspace is archived. Restore it to change its settings.",
  forbidden: "You don't have permission to change this workspace.",
};

function Divider() {
  return <div className="h-px bg-border" />;
}

function Row({ title, hint, danger, children }: { title: ReactNode; hint: ReactNode; danger?: boolean; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <div className={danger ? "text-sm font-semibold text-danger" : "text-sm font-semibold"}>{title}</div>
        <div className="text-xs text-fg-subtle">{hint}</div>
      </div>
      {children}
    </div>
  );
}

/** IANA zones for the timezone field's suggestions, filled after mount (ICU lists differ between server and browser). */
function useTimeZones(): string[] {
  const [zones, setZones] = useState<string[]>([]);
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- browser-only data, read once after mount
      setZones(Intl.supportedValuesOf("timeZone"));
    } catch {
      // Older engines: free text still works; the API validates the zone.
    }
  }, []);
  return zones;
}

/* ---- profile ---- */

const PROFILE_FIELDS = ["name", "timezone", "workspace_type"] as const;

export function WorkspaceProfilePanel({ workspace, canEdit }: { workspace: WorkspaceSettingsView; canEdit: boolean }) {
  const router = useRouter();
  const [name, setName] = useState(workspace.name);
  const [timezone, setTimezone] = useState(workspace.timezone);
  const [type, setType] = useState(workspace.workspace_type);
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<(typeof PROFILE_FIELDS)[number], string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const zones = useTimeZones();
  const listId = useId();

  const body: WorkspacePatch = {};
  if (name.trim() !== workspace.name) body.name = name.trim();
  if (timezone.trim() !== workspace.timezone) body.timezone = timezone.trim();
  if (type !== workspace.workspace_type) body.workspace_type = type as WorkspaceType;
  const changed = Object.keys(body).length > 0;
  const editable = canEdit && !workspace.archived;

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    setErrors({});
    setFormError(null);
    if (name.trim() === "") {
      setErrors({ name: "A workspace needs a name." });
      return;
    }
    setPending(true);
    const r = await settle(updateWorkspace({ client: browserApi, path: { workspace_id: workspace.id }, body }));
    setPending(false);
    if (!r.ok) {
      const split = formErrors(r.error, PROFILE_FIELDS);
      setErrors(split.fields);
      if (split.form.length) setFormError(WORKSPACE_ERRORS[r.error.code] ?? split.form.join(" "));
      return;
    }
    setSaved(true);
    router.refresh();
  }

  return (
    <Panel>
      <PanelHead title="Workspace" />
      <PanelBody>
        <form onSubmit={save} noValidate aria-label="Workspace profile" className="grid gap-3.5">
          {formError ? (
            <Notice tone="danger" role="alert">
              {formError}
            </Notice>
          ) : null}
          <div className="grid grid-cols-2 gap-3 max-narrow:grid-cols-1">
            <Field label="Name" error={errors.name}>
              <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} disabled={!editable} />
            </Field>
            <Field label="Slug" hint="Part of every link to this workspace, so it doesn't change.">
              <Input value={workspace.slug} readOnly disabled className="font-mono" />
            </Field>
            <Field label="Type" error={errors.workspace_type}>
              <Select value={type} onChange={(e) => setType(e.target.value)} disabled={!editable}>
                {(Object.keys(WORKSPACE_TYPE_LABEL) as WorkspaceType[]).map((t) => (
                  <option key={t} value={t}>
                    {WORKSPACE_TYPE_LABEL[t]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Timezone" hint="Schedules, quiet hours and every time Mera shows use it." error={errors.timezone}>
              <Input
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                list={listId}
                spellCheck={false}
                autoComplete="off"
                disabled={!editable}
                className="font-mono"
              />
            </Field>
            <datalist id={listId}>
              {zones.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
          </div>
          {editable ? (
            <div className="flex items-center gap-2.5">
              <Button type="submit" size="sm" disabled={pending || !changed}>
                {pending ? "Saving…" : "Save workspace"}
              </Button>
              {saved ? (
                <span role="status" className="text-caption text-fg-subtle">
                  Saved.
                </span>
              ) : null}
            </div>
          ) : (
            <p className="text-caption text-fg-subtle">
              {canEdit ? "Restore the workspace to change it." : "Only owners and admins can change the workspace."}
            </p>
          )}
        </form>
      </PanelBody>
    </Panel>
  );
}

/* ---- defaults ---- */

interface DefaultsDraft {
  cap: string;
  approvers: string;
  quietStart: string;
  quietEnd: string;
}

function draftFrom(settings: LevelSettings | null): DefaultsDraft {
  return {
    cap: settings?.daily_post_cap ? String(settings.daily_post_cap) : "",
    approvers: settings?.required_approvers ? String(settings.required_approvers) : "",
    quietStart: settings?.quiet_hours?.start ?? "",
    quietEnd: settings?.quiet_hours?.end ?? "",
  };
}

/**
 * The PATCH body for the fields that changed. A cleared field is sent as null
 * ("inherit the system default"); an untouched one is not sent at all, so a
 * value this form couldn't read is never overwritten by accident.
 */
export function defaultsPatch(
  initial: DefaultsDraft,
  draft: DefaultsDraft,
): { settings: LevelSettings; errors: Partial<Record<"cap" | "approvers" | "quiet", string>> } {
  const settings: LevelSettings = {};
  const errors: Partial<Record<"cap" | "approvers" | "quiet", string>> = {};
  if (draft.cap !== initial.cap) {
    const v = parseBoundedInt(draft.cap, 1, 50);
    if (v === "invalid") errors.cap = "A whole number from 1 to 50, or blank for the system default (5).";
    else settings.daily_post_cap = v;
  }
  if (draft.approvers !== initial.approvers) {
    const v = parseBoundedInt(draft.approvers, 1, 5);
    if (v === "invalid") errors.approvers = "A whole number from 1 to 5, or blank for the system default (1).";
    else settings.required_approvers = v;
  }
  if (draft.quietStart !== initial.quietStart || draft.quietEnd !== initial.quietEnd) {
    const s = draft.quietStart.trim();
    const e = draft.quietEnd.trim();
    if (s === "" && e === "") settings.quiet_hours = null;
    else if (!HHMM.test(s) || !HHMM.test(e)) errors.quiet = "Give both a start and an end as HH:MM, or clear both.";
    else settings.quiet_hours = { start: s, end: e };
  }
  return { settings, errors };
}

export function WorkspaceDefaultsPanel({
  workspace,
  settings,
  canEdit,
}: {
  workspace: WorkspaceSettingsView;
  /** The workspace's stored settings, or null when the API didn't return them. */
  settings: LevelSettings | null;
  canEdit: boolean;
}) {
  const router = useRouter();
  const initial = draftFrom(settings);
  const [draft, setDraft] = useState<DefaultsDraft>(initial);
  const [errors, setErrors] = useState<Partial<Record<"cap" | "approvers" | "quiet", string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const editable = canEdit && !workspace.archived;
  const quietId = useId();
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const set = (patch: Partial<DefaultsDraft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setSaved(false);
  };

  async function save(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    const { settings: body, errors: next } = defaultsPatch(initial, draft);
    setErrors(next);
    if (Object.keys(next).length > 0 || Object.keys(body).length === 0) return;
    setPending(true);
    const r = await settle(updateWorkspace({ client: browserApi, path: { workspace_id: workspace.id }, body: { settings: body } }));
    setPending(false);
    if (!r.ok) {
      setFormError(WORKSPACE_ERRORS[r.error.code] ?? displayMessage(r.error));
      return;
    }
    setSaved(true);
    router.refresh();
  }

  return (
    <Panel>
      <PanelHead title="Workspace defaults" actions={<span className="text-caption text-fg-subtle">groups and accounts can override</span>} />
      <PanelBody>
        <form onSubmit={save} noValidate aria-label="Workspace defaults" className="grid gap-3.5">
          {settings === null ? (
            <Notice>
              Current values can&apos;t be shown yet. Fields you leave blank are not changed; the effective value of each
              setting is in a group&apos;s “Resolved settings”.
            </Notice>
          ) : null}
          {formError ? (
            <Notice tone="danger" role="alert">
              {formError}
            </Notice>
          ) : null}
          <div className="grid grid-cols-2 gap-3 max-narrow:grid-cols-1">
            <Field label="Daily post cap" hint="Per account per day. Blank uses the system default (5)." error={errors.cap}>
              <Input inputMode="numeric" value={draft.cap} onChange={(e) => set({ cap: e.target.value })} placeholder="5" disabled={!editable} />
            </Field>
            <Field
              label="Required approvers"
              hint="Strictest wins: a group or account can require more, never fewer."
              error={errors.approvers}
            >
              <Input
                inputMode="numeric"
                value={draft.approvers}
                onChange={(e) => set({ approvers: e.target.value })}
                placeholder="1"
                disabled={!editable}
              />
            </Field>
          </div>
          <fieldset aria-labelledby={quietId} className="grid gap-1.5">
            <legend id={quietId} className="text-sm font-semibold text-fg">
              Quiet hours <span className="font-normal text-fg-subtle">· {workspace.timezone}</span>
            </legend>
            <div className="flex flex-wrap items-center gap-2">
              <Input
                type="time"
                aria-label="Quiet hours start"
                value={draft.quietStart}
                onChange={(e) => set({ quietStart: e.target.value })}
                disabled={!editable}
                className="w-32 font-mono"
              />
              <span aria-hidden="true" className="text-fg-subtle">
                –
              </span>
              <Input
                type="time"
                aria-label="Quiet hours end"
                value={draft.quietEnd}
                onChange={(e) => set({ quietEnd: e.target.value })}
                disabled={!editable}
                className="w-32 font-mono"
              />
            </div>
            <span className="text-caption text-fg-subtle">Nothing publishes, and no approval reminders go out, in this window.</span>
            {errors.quiet ? <span className="text-caption font-medium text-danger">{errors.quiet}</span> : null}
          </fieldset>
          <Divider />
          <Row title="Autopilot" hint="Assisted and Full Autopilot arrive in Phase 3.">
            <Tag tone="outline">Manual</Tag>
          </Row>
          {editable ? (
            <div className="flex items-center gap-2.5">
              <Button type="submit" size="sm" disabled={pending || !changed}>
                {pending ? "Saving…" : "Save defaults"}
              </Button>
              {saved ? (
                <span role="status" className="text-caption text-fg-subtle">
                  Saved.
                </span>
              ) : null}
            </div>
          ) : null}
        </form>
      </PanelBody>
      <PanelFoot>
        Most settings resolve to the most specific level that sets them. Approvers and autopilot resolve to the
        strictest, so a permissive default here never loosens a stricter group or account.
      </PanelFoot>
    </Panel>
  );
}

/* ---- danger zone ---- */

export function DangerZonePanel({ workspace, canArchive, canDelete }: { workspace: WorkspaceSettingsView; canArchive: boolean; canDelete: boolean }) {
  const router = useRouter();
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState<"archive" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const confirmId = useId();

  if (!canArchive && !canDelete) return null;

  async function toggleArchive() {
    setPending("archive");
    setError(null);
    const call = workspace.archived ? unarchiveWorkspace : archiveWorkspace;
    const r = await settle(call({ client: browserApi, path: { workspace_id: workspace.id } }));
    setPending(null);
    if (!r.ok) {
      setError(WORKSPACE_ERRORS[r.error.code] ?? displayMessage(r.error));
      return;
    }
    setConfirmArchive(false);
    router.refresh();
  }

  async function remove() {
    if (typed !== workspace.name) return;
    setPending("delete");
    setError(null);
    const r = await settle(deleteWorkspace({ client: browserApi, path: { workspace_id: workspace.id } }));
    if (!r.ok) {
      setPending(null);
      setError(WORKSPACE_ERRORS[r.error.code] ?? displayMessage(r.error));
      return;
    }
    // The workspace is gone from this user's list: start again from the root, which picks another.
    router.replace("/");
    router.refresh();
  }

  return (
    <Panel tone="danger">
      <PanelHead title="Danger zone" />
      <PanelBody className="grid gap-3.5">
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        {canArchive ? (
          workspace.archived ? (
            <Row title="Restore workspace" hint="Makes it writable again for every member.">
              <Button size="sm" onClick={toggleArchive} disabled={pending !== null}>
                {pending === "archive" ? "Restoring…" : "Restore"}
              </Button>
            </Row>
          ) : (
            <>
              <Row title="Archive workspace" hint="Stops automation and ingestion, keeps everything readable for reporting.">
                {confirmArchive ? null : (
                  <Button size="sm" onClick={() => setConfirmArchive(true)}>
                    Archive
                  </Button>
                )}
              </Row>
              {confirmArchive ? (
                <Notice
                  tone="warn"
                  title={`Archive ${workspace.name}?`}
                  actions={
                    <>
                      <Button size="sm" onClick={toggleArchive} disabled={pending !== null}>
                        {pending === "archive" ? "Archiving…" : `Archive ${workspace.name}`}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirmArchive(false)} disabled={pending !== null}>
                        Cancel
                      </Button>
                    </>
                  }
                >
                  Nothing can be created, changed or published until an owner or admin restores it.
                </Notice>
              ) : null}
            </>
          )
        ) : null}
        {canArchive && canDelete ? <Divider /> : null}
        {canDelete ? (
          <>
            <Row danger title="Delete workspace" hint="Removes it for every member. Only an owner can do this.">
              {deleting ? null : (
                <Button size="sm" variant="danger" onClick={() => setDeleting(true)}>
                  Delete
                </Button>
              )}
            </Row>
            {deleting ? (
              <div className="grid gap-2.5 rounded-md border border-[color-mix(in_srgb,var(--danger)_30%,transparent)] bg-danger-bg p-3.5">
                <p className="text-sm text-fg-muted">
                  Deleting <strong className="text-fg">{workspace.name}</strong> removes its accounts, groups, content and
                  history for everyone. This can&apos;t be undone from here.
                </p>
                <Field label={`Type “${workspace.name}” to confirm`} controlId={confirmId}>
                  <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
                </Field>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="danger" onClick={remove} disabled={typed !== workspace.name || pending !== null}>
                    {pending === "delete" ? "Deleting…" : `Delete ${workspace.name}`}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setDeleting(false);
                      setTyped("");
                    }}
                    disabled={pending !== null}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ) : null}
          </>
        ) : null}
      </PanelBody>
    </Panel>
  );
}
