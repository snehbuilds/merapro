"use client";

import { useSecondFactor } from "@/components/mera/second-factor";
import { changeMemberRole, removeMember, type Role } from "@mera/sdk";
import type { WorkspaceMember } from "@/lib/api/models";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { DataTable, dataTableColumns } from "@/components/mera/data-table";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { MEMBER_ROLES, roleLabel } from "@/lib/workspace";

/* Role order for sorting: most powerful first (docs/04 §2.6). */
const ROLE_RANK: Readonly<Record<string, number>> = { owner: 0, admin: 1, editor: 2, approver: 3, viewer: 4 };

/* API error codes the team actions can hit, in plain words. */
const MEMBER_ERRORS: Readonly<Record<string, string>> = {
  last_owner: "A workspace needs at least one owner. Make someone else owner first.",
  workspace_archived: "This workspace is archived, so its team can't change.",
  forbidden: "You don't have permission to change this member.",
};

export interface MemberRow extends WorkspaceMember {
  /** Workspace timezone for "Joined". */
  tz: string;
  isYou: boolean;
  workspaceId: string;
  /** The viewer may change roles and remove members (Admin+). */
  canManage: boolean;
  /** Archived workspace. */
  readOnly: boolean;
  /**
   * The member's scope in words (lib/workspace scopeSummary). Undefined for a
   * restricted member whose scope the viewer may not read (only Admin+ and
   * the member themself can).
   */
  scopeLabel?: string;
}

function errorText(code: string, fallback: string) {
  return MEMBER_ERRORS[code] ?? fallback;
}

function RoleCell({ row }: { row: MemberRow }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!row.canManage || row.isYou) return <Tag tone="outline">{roleLabel(row.role)}</Tag>;

  async function change(role: Role) {
    setPending(true);
    setError(null);
    const result = await settle(
      changeMemberRole({ client: browserApi, path: { workspace_id: row.workspaceId, user_id: row.user_id }, body: { role } }),
    );
    setPending(false);
    if (!result.ok) {
      setError(errorText(result.error.code, displayMessage(result.error)));
      return;
    }
    router.refresh();
  }

  return (
    <div className="grid max-w-[180px] gap-1">
      <Select
        aria-label={`Role for ${row.display_name}`}
        value={row.role}
        disabled={pending || row.readOnly}
        onChange={(e) => change(e.target.value as Role)}
        className="min-h-7 py-0.5 text-sm"
      >
        {MEMBER_ROLES.map((r) => (
          <option key={r} value={r}>
            {roleLabel(r)}
          </option>
        ))}
      </Select>
      {error ? (
        <span role="alert" className="text-caption text-danger">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function RemoveCell({ row }: { row: MemberRow }) {
  const router = useRouter();
  const { withSecondFactor } = useSecondFactor();
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!row.canManage || row.isYou) return null;

  async function remove() {
    setPending(true);
    setError(null);
    const result = await withSecondFactor((headers) =>
      settle(removeMember({ client: browserApi, headers, path: { workspace_id: row.workspaceId, user_id: row.user_id } })),
    );
    setPending(false);
    if (!result.ok) {
      setError(errorText(result.error.code, displayMessage(result.error)));
      return;
    }
    setConfirming(false);
    router.refresh();
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      {confirming ? (
        <>
          <Button size="sm" variant="danger" onClick={remove} disabled={pending}>
            {pending ? "Removing…" : `Remove ${row.display_name}`}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={pending}>
            Cancel
          </Button>
        </>
      ) : (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setConfirming(true)}
          disabled={row.readOnly}
          aria-label={`Remove ${row.display_name}`}
        >
          Remove
        </Button>
      )}
      {error ? (
        <span role="alert" className="basis-full text-right text-caption text-danger">
          {error}
        </span>
      ) : null}
    </div>
  );
}

const col = dataTableColumns<MemberRow>();

const columns = [
  col.accessor("display_name", {
    header: "Member",
    cell: ({ row }) => (
      <div className="grid gap-0.5">
        <span className="font-medium text-fg">
          {row.original.display_name}
          {row.original.isYou ? <span className="ml-1 text-xs font-normal text-fg-subtle">(you)</span> : null}
        </span>
        <span className="text-xs text-fg-subtle">{row.original.email}</span>
      </div>
    ),
  }),
  col.accessor((m) => ROLE_RANK[m.role] ?? 99, {
    id: "role",
    header: "Role",
    cell: ({ row }) => <RoleCell row={row.original} />,
  }),
  col.accessor("scope_restricted", {
    header: "Scope",
    enableSorting: false,
    cell: ({ row }) => {
      const m = row.original;
      if (!m.scope_restricted) return <span className="text-sm">Whole workspace</span>;
      return m.scopeLabel ? (
        <span className="grid max-w-[280px] gap-0.5 text-sm">
          <span className="text-xs text-fg-subtle">Restricted to</span>
          {m.scopeLabel}
        </span>
      ) : (
        <span className="text-sm text-fg-subtle">
          <span aria-hidden="true">—</span>
          <span className="sr-only">Restricted; only admins can see to what</span>
        </span>
      );
    },
  }),
  col.accessor((m) => m.joined_at ?? "", {
    id: "joined",
    header: "Joined",
    cell: ({ row }) =>
      row.original.joined_at ? (
        <Time value={row.original.joined_at} tz={row.original.tz} format="date" />
      ) : (
        <span className="text-sm text-fg-subtle">—</span>
      ),
  }),
  col.display({
    id: "actions",
    header: () => <span className="sr-only">Actions</span>,
    meta: { align: "end" },
    cell: ({ row }) => <RemoveCell row={row.original} />,
  }),
];

export function MembersTable({ members, workspaceName }: { members: MemberRow[]; workspaceName: string }) {
  return (
    <DataTable
      columns={columns}
      data={members}
      caption={`Members of ${workspaceName}`}
      initialSorting={[{ id: "role", desc: false }]}
      getRowId={(m) => m.user_id}
    />
  );
}
