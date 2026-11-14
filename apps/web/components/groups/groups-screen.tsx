"use client";

import { createGroup, deleteGroup, updateGroup, type AccountOut, type GroupOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { Icon } from "@/components/icons";
import { GroupDot } from "@/components/mera/group-dot";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { displayMessage, formErrors } from "@/lib/api/errors";
import { GROUP_ERROR_TEXT, groupSwatch, platformSummary, staggerConflict } from "@/lib/groups";
import { SCREENS } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { MemberTable } from "./member-table";
import { ResolvedSettingsPanel } from "./resolved-settings-panel";
import { PostingTimesPanel } from "./posting-times-panel";
import { StaggerPanel } from "./stagger-panel";

/*
 * Account Groups (groups.html, ADR-0012): the group list on the left, the
 * selected group on the right — members in publish order, stagger, and the
 * settings it resolves to. Editors and up manage groups; others read.
 */

export interface GroupsScreenProps {
  workspace: { id: string; slug: string; name: string; timezone: string; archived: boolean };
  groups: GroupOut[];
  accounts: AccountOut[];
  /** Editor+ (MANAGE_GROUPS). */
  canManage: boolean;
}

/** A key that changes whenever the group's server state does (resets local state, refetches resolved settings). */
function groupVersion(g: GroupOut): string {
  return [
    g.name,
    g.stagger_mode,
    g.stagger_min_seconds,
    g.stagger_max_seconds,
    JSON.stringify(g.settings),
    ...g.members.map((m) => `${m.social_account_id}:${m.position}:${m.enabled_by_default}:${m.status}`),
  ].join("|");
}

function CreateGroupForm({ workspaceId, onCreated, onCancel }: { workspaceId: string; onCreated: (g: GroupOut) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<{ name?: string; description?: string; form?: string }>({});

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setPending(true);
    const r = await settle(
      createGroup({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspaceId },
        body: { name: name.trim(), description: description.trim() || null },
      }),
    );
    setPending(false);
    if (!r.ok) {
      if (r.error.code === "group_name_taken") {
        setErrors({ name: r.error.fieldErrors.name ?? r.error.message });
        return;
      }
      const split = formErrors(r.error, ["name", "description"] as const);
      setErrors({ ...split.fields, form: split.form.length ? split.form.map((m) => GROUP_ERROR_TEXT[r.error.code] ?? m).join(" ") : undefined });
      return;
    }
    onCreated(r.data);
  }

  return (
    <form onSubmit={submit} noValidate aria-label="New group" className="grid gap-3 border-b border-border p-3.5">
      {errors.form ? (
        <Notice tone="danger" role="alert">
          {errors.form}
        </Notice>
      ) : null}
      <Field label="Name" error={errors.name}>
        <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoFocus />
      </Field>
      <Field label="Description" hint="Optional. What these accounts have in common." error={errors.description}>
        <Input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={280} />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={pending || name.trim() === ""}>
          {pending ? "Creating…" : "Create group"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function GroupHead({ group, workspaceId, canEdit, onDeleted }: { group: GroupOut; workspaceId: string; canEdit: boolean; onDeleted: () => void }) {
  const router = useRouter();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(group.name);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameId = useId();

  async function rename(e: FormEvent) {
    e.preventDefault();
    if (name.trim() === "" || name.trim() === group.name) {
      setRenaming(false);
      return;
    }
    setPending(true);
    setError(null);
    const r = await settle(
      updateGroup({ client: browserApi, path: { workspace_id: workspaceId, group_id: group.id }, body: { name: name.trim() } }),
    );
    setPending(false);
    if (!r.ok) {
      setError(r.error.fieldErrors.name ?? GROUP_ERROR_TEXT[r.error.code] ?? displayMessage(r.error));
      return;
    }
    setRenaming(false);
    router.refresh();
  }

  async function remove() {
    setPending(true);
    setError(null);
    const r = await settle(deleteGroup({ client: browserApi, path: { workspace_id: workspaceId, group_id: group.id } }));
    setPending(false);
    if (!r.ok) {
      setError(GROUP_ERROR_TEXT[r.error.code] ?? displayMessage(r.error));
      return;
    }
    onDeleted();
    router.refresh();
  }

  const swatch = groupSwatch(group);
  return (
    <>
      {renaming ? (
        <form onSubmit={rename} className="flex flex-wrap items-center gap-2 border-b border-border px-3.5 py-2.5" aria-label="Rename group">
          <GroupDot group={group.id} style={{ background: swatch }} />
          <label htmlFor={nameId} className="sr-only">
            Group name
          </label>
          <Input
            id={nameId}
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            autoFocus
            aria-invalid={error ? true : undefined}
            className="min-h-7 max-w-72 py-0.5"
          />
          <Button type="submit" size="sm" variant="primary" disabled={pending}>
            {pending ? "Saving…" : "Save"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setRenaming(false);
              setName(group.name);
              setError(null);
            }}
          >
            Cancel
          </Button>
        </form>
      ) : (
        <PanelHead
          title={
            <span className="inline-flex items-center gap-2">
              <GroupDot group={group.id} style={{ background: swatch }} />
              {group.name}
              {group.enabled ? null : <Tag tone="neutral">Off</Tag>}
            </span>
          }
          actions={
            canEdit ? (
              <span className="flex gap-1.5">
                <Button size="sm" onClick={() => setRenaming(true)}>
                  Rename
                </Button>
                {confirmDelete ? null : (
                  <Button size="sm" variant="danger" onClick={() => setConfirmDelete(true)}>
                    Delete group
                  </Button>
                )}
              </span>
            ) : null
          }
        />
      )}
      {group.description ? <p className="border-b border-border px-3.5 py-2 text-sm text-fg-muted">{group.description}</p> : null}
      {confirmDelete ? (
        <div className="border-b border-border p-3.5">
          <Notice
            tone="danger"
            title={`Delete ${group.name}?`}
            actions={
              <>
                <Button size="sm" variant="danger" onClick={remove} disabled={pending}>
                  {pending ? "Deleting…" : `Delete ${group.name}`}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)} disabled={pending}>
                  Cancel
                </Button>
              </>
            }
          >
            Its {group.member_count === 1 ? "account stays" : `${group.member_count} accounts stay`} connected and in any
            other groups. Anything that inherited from this group (posting slots, stagger, approval and autopilot
            settings) falls back to the workspace.
          </Notice>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="border-b border-border px-3.5 py-2 text-caption text-danger">
          {error}
        </p>
      ) : null}
    </>
  );
}

export function GroupsScreen({ workspace, groups, accounts, canManage }: GroupsScreenProps) {
  const router = useRouter();
  const canEdit = canManage && !workspace.archived;
  const [selectedId, setSelectedId] = useState<string | undefined>(groups[0]?.id);
  const [creating, setCreating] = useState(false);
  const selected = groups.find((g) => g.id === selectedId) ?? groups[0];
  const accountCount = new Set(groups.flatMap((g) => g.members.map((m) => m.social_account_id))).size;
  const conflict = selected ? staggerConflict(selected.members) : null;

  const newGroup = canEdit ? (
    <Button variant="primary" className="shrink-0 max-narrow:w-8 max-narrow:px-0" onClick={() => setCreating(true)} disabled={creating}>
      <Icon name="plus" size="sm" />
      <span className="max-narrow:sr-only">New group</span>
    </Button>
  ) : null;

  return (
    <>
      <Topbar
        title={SCREENS.groups.title}
        sub={`${groups.length} ${groups.length === 1 ? "group" : "groups"} · ${accountCount} ${accountCount === 1 ? "account" : "accounts"}`}
        actionNode={newGroup}
      />
      <main id="main" tabIndex={-1} className="flex-1 p-6 outline-none max-rail:p-4">
        <div className="grid grid-cols-[320px_minmax(0,1fr)] items-start gap-3.5 max-wide:grid-cols-[270px_minmax(0,1fr)] max-rail:grid-cols-1">
          <Panel>
            <PanelHead title="Groups" />
            {creating ? (
              <CreateGroupForm
                workspaceId={workspace.id}
                onCancel={() => setCreating(false)}
                onCreated={(g) => {
                  setCreating(false);
                  setSelectedId(g.id);
                  router.refresh();
                }}
              />
            ) : null}
            <PanelBody flush>
              {groups.length === 0 ? (
                <EmptyState
                  icon="groups"
                  tone="quiet"
                  title="No groups yet"
                  description={
                    canEdit
                      ? "Group accounts you usually post to together, like “Tech News” = two LinkedIn and two X accounts."
                      : "Editors group the accounts this workspace usually posts to together."
                  }
                />
              ) : (
                <ul aria-label="Account groups">
                  {groups.map((g) => (
                    <li key={g.id}>
                      <button
                        type="button"
                        aria-current={g.id === selected?.id ? "true" : undefined}
                        onClick={() => setSelectedId(g.id)}
                        className={cn(
                          "grid w-full gap-[5px] border-b border-border px-3.5 py-3 text-left hover:bg-surface-hover",
                          "aria-[current=true]:bg-accent-soft",
                        )}
                      >
                        <span className="flex items-center gap-2">
                          <GroupDot group={g.id} style={{ background: groupSwatch(g) }} />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{g.name}</span>
                          <Tag>
                            <span className="sr-only">Accounts: </span>
                            {g.member_count}
                          </Tag>
                        </span>
                        <span className="truncate text-caption text-fg-muted">{platformSummary(g.members)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </PanelBody>
            <PanelFoot>An account can belong to several groups, with its own place in each one&apos;s order.</PanelFoot>
          </Panel>

          {selected ? (
            <section aria-label={`Group: ${selected.name}`} className="grid gap-3.5" key={selected.id}>
              <Panel>
                <GroupHead
                  key={groupVersion(selected)}
                  group={selected}
                  workspaceId={workspace.id}
                  canEdit={canEdit}
                  onDeleted={() => setSelectedId(groups.find((g) => g.id !== selected.id)?.id)}
                />
                <MemberTable
                  key={groupVersion(selected)}
                  group={selected}
                  accounts={accounts}
                  workspaceId={workspace.id}
                  canEdit={canEdit}
                />
              </Panel>

              {conflict ? (
                <Notice tone="warn" title={`${conflict.count} ${conflict.label} accounts in this group.`}>
                  Staggering is required, so “All at once” is blocked for this group until only one of them remains.
                  Platforms treat near-simultaneous, similar posts across accounts as spam.
                </Notice>
              ) : null}

              <StaggerPanel key={`stagger-${groupVersion(selected)}`} group={selected} workspaceId={workspace.id} canEdit={canEdit} />

              <PostingTimesPanel
                key={`times-${groupVersion(selected)}`}
                group={selected}
                workspaceId={workspace.id}
                timezone={workspace.timezone}
                canEdit={canEdit}
              />

              <ResolvedSettingsPanel workspaceId={workspace.id} groupId={selected.id} version={groupVersion(selected)} />
            </section>
          ) : null}
        </div>
      </main>
    </>
  );
}
