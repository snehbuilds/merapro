"use client";

import { deletePolicy, putPolicy, type PolicyOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * Approval policies (P2-M2.8): how many people approve, which people, who hears
 * about an escalation, and the timers, at workspace, group or account level.
 * Policies resolve strictest-wins (ADR-0014): a group can only tighten the
 * workspace, never loosen it.
 */

type ScopeType = "workspace" | "account_group" | "social_account";

export interface PoliciesPanelProps {
  workspaceId: string;
  policies: PolicyOut[];
  groups: { id: string; name: string }[];
  accounts: { id: string; nickname: string }[];
  /** Members who can approve. */
  approvers: { id: string; name: string }[];
  canEdit: boolean;
}

interface Draft {
  scopeType: ScopeType;
  scopeId: string;
  required: number;
  quorum: "any_of" | "all_of";
  approverIds: string[];
  escalateIds: string[];
  expiresHours: number;
}

const EMPTY: Draft = { scopeType: "account_group", scopeId: "", required: 1, quorum: "any_of", approverIds: [], escalateIds: [], expiresHours: 12 };

function People({ label, all, picked, onChange }: { label: string; all: { id: string; name: string }[]; picked: string[]; onChange: (ids: string[]) => void }) {
  return (
    <fieldset className="grid gap-1">
      <legend className="mb-1 text-caption font-medium text-fg">{label}</legend>
      {all.length === 0 ? <p className="text-caption text-fg-subtle">Nobody else can approve yet.</p> : null}
      {all.map((p) => (
        <label key={p.id} className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={picked.includes(p.id)}
            onChange={(e) => onChange(e.target.checked ? [...picked, p.id] : picked.filter((x) => x !== p.id))}
          />
          {p.name}
        </label>
      ))}
    </fieldset>
  );
}

export function PoliciesPanel({ workspaceId, policies, groups, accounts, approvers, canEdit }: PoliciesPanelProps) {
  const router = useRouter();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const names = new Map<string, string>([
    ...groups.map((g) => [g.id, g.name] as const),
    ...accounts.map((a) => [a.id, a.nickname] as const),
    ...approvers.map((p) => [p.id, p.name] as const),
  ]);
  const scopeName = (p: PolicyOut) => (p.scope_type === "workspace" ? "Whole workspace" : names.get(p.scope_id ?? "") ?? "A removed scope");

  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    setError(null);
    const r = await settle(
      putPolicy({
        client: browserApi,
        path: { workspace_id: workspaceId },
        body: {
          scope_type: draft.scopeType,
          scope_id: draft.scopeType === "workspace" ? null : draft.scopeId || null,
          required_approver_count: draft.required,
          quorum_mode: draft.quorum,
          approver_user_ids: draft.approverIds,
          escalate_to_user_ids: draft.escalateIds,
          expires_after_minutes: Math.round(draft.expiresHours * 60),
        },
      }),
    );
    setBusy(false);
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    setDraft(null);
    router.refresh();
  }

  async function remove(p: PolicyOut) {
    const r = await settle(deletePolicy({ client: browserApi, path: { workspace_id: workspaceId, policy_id: p.id } }));
    if (!r.ok) setError(displayMessage(r.error));
    else router.refresh();
  }

  return (
    <Panel aria-labelledby="policies-title">
      <PanelHead
        title={<span id="policies-title">Approval policies</span>}
        actions={
          canEdit && !draft ? (
            <Button size="sm" onClick={() => setDraft(EMPTY)}>
              Add policy
            </Button>
          ) : undefined
        }
      />
      {error ? (
        <PanelBody>
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        </PanelBody>
      ) : null}
      {draft ? (
        <PanelBody className="border-b border-border">
          <form className="grid gap-3" onSubmit={save} aria-label="Approval policy">
            <div className="flex flex-wrap gap-3">
              <Field label="Applies to">
                <Select value={draft.scopeType} onChange={(e) => setDraft({ ...draft, scopeType: e.target.value as ScopeType, scopeId: "" })}>
                  <option value="workspace">Whole workspace</option>
                  <option value="account_group">A group</option>
                  <option value="social_account">An account</option>
                </Select>
              </Field>
              {draft.scopeType !== "workspace" ? (
                <Field label={draft.scopeType === "account_group" ? "Group" : "Account"}>
                  <Select value={draft.scopeId} onChange={(e) => setDraft({ ...draft, scopeId: e.target.value })} required>
                    <option value="">Choose…</option>
                    {(draft.scopeType === "account_group" ? groups.map((g) => ({ id: g.id, label: g.name })) : accounts.map((a) => ({ id: a.id, label: a.nickname }))).map(
                      (o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ),
                    )}
                  </Select>
                </Field>
              ) : null}
              <Field label="Approvals needed">
                <Input type="number" min={1} max={5} value={draft.required} onChange={(e) => setDraft({ ...draft, required: Number(e.target.value) || 1 })} className="w-24" />
              </Field>
              <Field label="Who must approve">
                <Select value={draft.quorum} onChange={(e) => setDraft({ ...draft, quorum: e.target.value as Draft["quorum"] })}>
                  <option value="any_of">Any of them</option>
                  <option value="all_of">All of them</option>
                </Select>
              </Field>
              <Field label="Expires after (hours)">
                <Input type="number" min={1} max={168} value={draft.expiresHours} onChange={(e) => setDraft({ ...draft, expiresHours: Number(e.target.value) || 12 })} className="w-24" />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-4 max-narrow:grid-cols-1">
              <People label="Named approvers (empty: anyone who can approve)" all={approvers} picked={draft.approverIds} onChange={(ids) => setDraft({ ...draft, approverIds: ids })} />
              <People label="Escalate to (empty: Admins)" all={approvers} picked={draft.escalateIds} onChange={(ids) => setDraft({ ...draft, escalateIds: ids })} />
            </div>
            <div className="flex gap-2">
              <Button type="submit" variant="primary" disabled={busy || (draft.scopeType !== "workspace" && !draft.scopeId)}>
                {busy ? "Saving…" : "Save policy"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setDraft(null)}>
                Cancel
              </Button>
            </div>
          </form>
        </PanelBody>
      ) : null}
      <PanelBody flush>
        {policies.length === 0 ? (
          <EmptyState icon="approvals" tone="quiet" title="One approval from anyone who can approve (the default)" className="py-6" />
        ) : (
          <ul aria-label="Policies">
            {policies.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 border-b border-border px-3.5 py-2.5 last:border-b-0">
                <span className="min-w-0 flex-1 text-sm font-medium">{scopeName(p)}</span>
                <Tag tone="outline">
                  {p.required_approver_count} {p.quorum_mode === "all_of" ? "· all of" : "· any of"}
                </Tag>
                {p.approver_user_ids.length ? (
                  <span className="text-caption text-fg-muted">by {p.approver_user_ids.map((u) => names.get(u) ?? "someone").join(", ")}</span>
                ) : null}
                {(p.escalate_to_user_ids ?? []).length ? (
                  <span className="text-caption text-fg-muted">escalates to {(p.escalate_to_user_ids ?? []).map((u) => names.get(u) ?? "someone").join(", ")}</span>
                ) : null}
                {canEdit ? (
                  <Button size="sm" variant="ghost" onClick={() => void remove(p)} aria-label={`Remove policy for ${scopeName(p)}`}>
                    Remove
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </PanelBody>
      <PanelFoot>Policies combine strictest-wins: a group or account can tighten the workspace&apos;s rules, never loosen them.</PanelFoot>
    </Panel>
  );
}
