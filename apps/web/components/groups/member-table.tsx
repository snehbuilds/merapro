"use client";

import { addGroupMembers, removeGroupMember, updateGroupMember, type AccountOut, type GroupOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useId, useState, type DragEvent } from "react";
import { Icon } from "@/components/icons";
import { AccountChip } from "@/components/mera/account-chip";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { PanelFoot } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Switch } from "@/components/ui/switch";
import { accountRef, accountTypeLabel, liveAccounts, platformLabel } from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage, type ApiError } from "@/lib/api/errors";
import type { GroupMember } from "@/lib/api/models";
import { GROUP_ERROR_TEXT, moveMember, orderedMembers, stepMove } from "@/lib/groups";
import { cn } from "@/lib/utils";

/*
 * groups.html member table: publish order (which drives the stagger
 * sequence), "on by default", add and remove. Reordering works by drag and by
 * the up / down buttons, which are the keyboard path; both send
 * PATCH .../members/{id} {position} and update the order optimistically.
 */

function errorText(error: ApiError): string {
  return GROUP_ERROR_TEXT[error.code] ?? displayMessage(error);
}

const th =
  "border-b border-border-strong px-3 py-[9px] text-left font-label text-[11.5px] font-semibold tracking-[0.09em] whitespace-nowrap text-fg-subtle uppercase first:pl-3.5 last:pr-3.5";
const td = "border-b border-border px-3 py-2.5 align-middle first:pl-3.5 last:pr-3.5";

export function MemberTable({
  group,
  accounts,
  workspaceId,
  canEdit,
}: {
  group: GroupOut;
  /** Every account in the workspace, for "Add accounts" and account types. */
  accounts: readonly AccountOut[];
  workspaceId: string;
  canEdit: boolean;
}) {
  const router = useRouter();
  const [members, setMembers] = useState<GroupMember[]>(() => orderedMembers(group.members));
  const [error, setError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const idBase = useId();
  const byId = new Map(accounts.map((a) => [a.id, a]));
  const path = { workspace_id: workspaceId, group_id: group.id };

  async function move(from: number, to: number, focusAfter?: "up" | "down") {
    const next = moveMember(members, from, to);
    if (!next) return;
    const moved = next[to]!;
    const before = members;
    setMembers(next);
    setError(null);
    setAnnounce(`${moved.nickname} moved to position ${to + 1} of ${next.length}.`);
    if (focusAfter) {
      // Keep focus on the moved row's control; at an end, its other arrow.
      requestAnimationFrame(() => {
        const atEnd = (focusAfter === "up" && to === 0) || (focusAfter === "down" && to === next.length - 1);
        const which = atEnd ? (focusAfter === "up" ? "down" : "up") : focusAfter;
        document.getElementById(`${idBase}-${moved.social_account_id}-${which}`)?.focus();
      });
    }
    const r = await settle(
      updateGroupMember({ client: browserApi, path: { ...path, account_id: moved.social_account_id }, body: { position: to } }),
    );
    if (!r.ok) {
      setMembers(before);
      setError(errorText(r.error));
      return;
    }
    setMembers(orderedMembers(r.data.members));
    router.refresh();
  }

  async function toggleDefault(member: GroupMember, on: boolean) {
    setMembers((list) => list.map((m) => (m.social_account_id === member.social_account_id ? { ...m, enabled_by_default: on } : m)));
    setError(null);
    const r = await settle(
      updateGroupMember({ client: browserApi, path: { ...path, account_id: member.social_account_id }, body: { enabled_by_default: on } }),
    );
    if (!r.ok) {
      setMembers((list) =>
        list.map((m) => (m.social_account_id === member.social_account_id ? { ...m, enabled_by_default: !on } : m)),
      );
      setError(errorText(r.error));
      return;
    }
    router.refresh();
  }

  async function remove(member: GroupMember) {
    setError(null);
    const r = await settle(removeGroupMember({ client: browserApi, path: { ...path, account_id: member.social_account_id } }));
    if (!r.ok) {
      setError(errorText(r.error));
      return;
    }
    setMembers(orderedMembers(r.data.members));
    setAnnounce(`${member.nickname} removed from ${group.name}.`);
    router.refresh();
  }

  function onDragStart(e: DragEvent, index: number) {
    setDragFrom(index);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(index));
  }
  function onDragOver(e: DragEvent, index: number) {
    if (dragFrom === null) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragOver !== index) setDragOver(index);
  }
  function onDrop(e: DragEvent, index: number) {
    e.preventDefault();
    const from = dragFrom;
    setDragFrom(null);
    setDragOver(null);
    if (from !== null) void move(from, index);
  }
  function onDragEnd() {
    setDragFrom(null);
    setDragOver(null);
  }

  const memberIds = new Set(members.map((m) => m.social_account_id));
  const addable = liveAccounts(accounts).filter((a) => !memberIds.has(a.id));

  return (
    <>
      <p aria-live="polite" className="sr-only">
        {announce}
      </p>
      {error ? (
        <div className="border-b border-border p-3.5">
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        </div>
      ) : null}
      {members.length === 0 ? (
        <p className="px-3.5 py-6 text-center text-sm text-fg-muted">
          No accounts in this group yet.{canEdit ? " Add some to post to them together." : ""}
        </p>
      ) : (
        <div role="region" aria-label={`Accounts in ${group.name}`} tabIndex={0} className="overflow-x-auto">
          <table className="w-full border-collapse text-sm max-rail:min-w-max [&_tbody_tr:last-child>td]:border-b-0">
            <caption className="sr-only">Accounts in {group.name}, in publish order</caption>
            <thead>
              <tr>
                <th scope="col" className={cn(th, "w-10")}>
                  <span className="sr-only">Order</span>
                </th>
                <th scope="col" className={th}>
                  Account
                </th>
                <th scope="col" className={th}>
                  Platform
                </th>
                <th scope="col" className={th}>
                  Status
                </th>
                <th scope="col" className={cn(th, "text-right")}>
                  On by default
                </th>
                {canEdit ? (
                  <th scope="col" className={cn(th, "text-right")}>
                    <span className="sr-only">Reorder and remove</span>
                  </th>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {members.map((m, i) => {
                const acct = byId.get(m.social_account_id);
                const type = acct ? accountTypeLabel(acct.account_type).toLowerCase() : undefined;
                return (
                  <tr
                    key={m.social_account_id}
                    draggable={canEdit}
                    onDragStart={canEdit ? (e) => onDragStart(e, i) : undefined}
                    onDragOver={canEdit ? (e) => onDragOver(e, i) : undefined}
                    onDrop={canEdit ? (e) => onDrop(e, i) : undefined}
                    onDragEnd={canEdit ? onDragEnd : undefined}
                    data-dragging={dragFrom === i || undefined}
                    data-drop-target={(dragOver === i && dragFrom !== i) || undefined}
                    className={cn(
                      "hover:bg-surface-hover data-[dragging]:opacity-50",
                      "data-[drop-target]:shadow-[inset_0_2px_0_var(--accent)]",
                      canEdit && "cursor-grab active:cursor-grabbing",
                    )}
                  >
                    <td className={cn(td, "tabular font-mono text-xs text-fg-subtle")}>
                      <span className="inline-flex items-center gap-1.5">
                        {canEdit ? <Icon name="menu" size="sm" className="text-fg-subtle" /> : null}
                        {i + 1}
                      </span>
                    </td>
                    <td className={td}>
                      <AccountChip account={accountRef({ ...m, id: m.social_account_id })} />
                    </td>
                    <td className={cn(td, "text-fg-muted")}>
                      {platformLabel(m.platform)}
                      {type ? ` · ${type}` : ""}
                    </td>
                    <td className={td}>
                      <StatusPill kind="account" state={m.status} />
                    </td>
                    <td className={cn(td, "text-right")}>
                      <Switch
                        checked={m.enabled_by_default}
                        disabled={!canEdit}
                        onCheckedChange={(on) => toggleDefault(m, on)}
                        aria-label={`${m.nickname} on by default`}
                      />
                    </td>
                    {canEdit ? (
                      <td className={cn(td, "text-right whitespace-nowrap")}>
                        <span className="inline-flex items-center gap-1">
                          <Button
                            id={`${idBase}-${m.social_account_id}-up`}
                            size="icon-sm"
                            variant="ghost"
                            disabled={i === 0}
                            onClick={() => {
                              const step = stepMove(i, "up", members.length);
                              if (step) void move(step.from, step.to, "up");
                            }}
                            aria-label={`Move ${m.nickname} up`}
                          >
                            <Icon name="chevdown" size="sm" className="rotate-180" />
                          </Button>
                          <Button
                            id={`${idBase}-${m.social_account_id}-down`}
                            size="icon-sm"
                            variant="ghost"
                            disabled={i === members.length - 1}
                            onClick={() => {
                              const step = stepMove(i, "down", members.length);
                              if (step) void move(step.from, step.to, "down");
                            }}
                            aria-label={`Move ${m.nickname} down`}
                          >
                            <Icon name="chevdown" size="sm" />
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => remove(m)} aria-label={`Remove ${m.nickname} from ${group.name}`}>
                            Remove
                          </Button>
                        </span>
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {adding ? (
        <AddAccounts
          group={group}
          workspaceId={workspaceId}
          candidates={addable}
          onDone={(updated) => {
            setAdding(false);
            if (updated) {
              setMembers(orderedMembers(updated.members));
              router.refresh();
            }
          }}
        />
      ) : null}
      <PanelFoot className="flex flex-wrap items-center justify-between gap-3">
        <span>
          {canEdit
            ? "Drag, or use the arrows, to reorder. Order drives the stagger sequence."
            : "Order drives the stagger sequence."}
        </span>
        {canEdit && !adding ? (
          <Button size="sm" onClick={() => setAdding(true)} disabled={addable.length === 0} title={addable.length === 0 ? "Every connected account is already in this group." : undefined}>
            <Icon name="plus" size="sm" />
            Add accounts
          </Button>
        ) : null}
      </PanelFoot>
    </>
  );
}

function AddAccounts({
  group,
  workspaceId,
  candidates,
  onDone,
}: {
  group: GroupOut;
  workspaceId: string;
  candidates: readonly AccountOut[];
  onDone: (updated: GroupOut | null) => void;
}) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const legendId = useId();

  const toggle = (id: string) => setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));

  async function add() {
    if (chosen.length === 0) return;
    setPending(true);
    setError(null);
    const r = await settle(
      addGroupMembers({
        client: browserApi,
        path: { workspace_id: workspaceId, group_id: group.id },
        body: { social_account_ids: chosen },
      }),
    );
    setPending(false);
    if (!r.ok) {
      // stagger_required: adding a second account on one platform to an "all at once" group.
      setError(r.error.fieldErrors.stagger_mode ?? errorText(r.error));
      return;
    }
    onDone(r.data);
  }

  return (
    <div className="grid gap-3 border-t border-border p-3.5">
      <fieldset aria-labelledby={legendId} className="grid gap-2">
        <legend id={legendId} className="mb-1 text-sm font-semibold">
          Add accounts to {group.name}
        </legend>
        <ul className="grid gap-1">
          {candidates.map((a) => {
            const id = `${legendId}-${a.id}`;
            return (
              <li key={a.id}>
                <label htmlFor={id} className="flex cursor-pointer items-center gap-2.5 rounded-sm px-1.5 py-1 hover:bg-surface-hover">
                  <input
                    id={id}
                    type="checkbox"
                    checked={chosen.includes(a.id)}
                    onChange={() => toggle(a.id)}
                    className="size-4 accent-(--accent)"
                  />
                  <AccountChip account={accountRef(a)} />
                  <span className="font-mono text-xs text-fg-subtle">{a.handle}</span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>
      {error ? (
        <Notice tone="danger" role="alert">
          {error}
        </Notice>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" onClick={add} disabled={pending || chosen.length === 0}>
          {pending ? "Adding…" : chosen.length > 1 ? `Add ${chosen.length} accounts` : "Add account"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onDone(null)} disabled={pending}>
          Cancel
        </Button>
        <span className="text-caption text-fg-subtle">New accounts join at the end of the order.</span>
      </div>
    </div>
  );
}
