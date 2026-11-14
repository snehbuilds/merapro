"use client";

import { getMemberScopes, setMemberScopes } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Field, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * Member scopes (P2-M2.1): narrow a member to some groups and accounts. A scope
 * narrows a role, never widens it; nothing ticked means the whole workspace.
 * Admins and Owners are never scoped.
 */

export interface ScopeEditorProps {
  workspaceId: string;
  members: { id: string; name: string; role: string }[];
  groups: { id: string; name: string }[];
  accounts: { id: string; nickname: string }[];
}

export function ScopeEditor({ workspaceId, members, groups, accounts }: ScopeEditorProps) {
  const router = useRouter();
  const scopable = members.filter((m) => m.role !== "owner" && m.role !== "admin");
  const [memberId, setMemberId] = useState("");
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ tone: "info" | "danger"; text: string } | null>(null);

  async function pick(id: string) {
    setMemberId(id);
    setMessage(null);
    if (!id) return;
    setLoading(true);
    const r = await settle(getMemberScopes({ client: browserApi, path: { workspace_id: workspaceId, user_id: id } }));
    setLoading(false);
    if (!r.ok) {
      setMessage({ tone: "danger", text: displayMessage(r.error) });
      return;
    }
    setGroupIds(r.data.account_group_ids);
    setAccountIds(r.data.social_account_ids);
  }

  async function save() {
    setMessage(null);
    const r = await settle(
      setMemberScopes({
        client: browserApi,
        path: { workspace_id: workspaceId, user_id: memberId },
        body: { account_group_ids: groupIds, social_account_ids: accountIds },
      }),
    );
    if (!r.ok) {
      setMessage({ tone: "danger", text: displayMessage(r.error) });
      return;
    }
    setMessage({ tone: "info", text: groupIds.length || accountIds.length ? "Scope saved." : "Scope removed: the whole workspace." });
    router.refresh();
  }

  const toggle = (list: string[], id: string, on: boolean) => (on ? [...list, id] : list.filter((x) => x !== id));

  return (
    <Panel aria-labelledby="scopes-title">
      <PanelHead title={<span id="scopes-title">Member scopes</span>} />
      <PanelBody className="grid gap-3">
        <Field label="Member">
          <Select value={memberId} onChange={(e) => void pick(e.target.value)}>
            <option value="">Choose a member…</option>
            {scopable.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </Select>
        </Field>
        {message ? (
          <Notice tone={message.tone} role={message.tone === "danger" ? "alert" : "status"}>
            {message.text}
          </Notice>
        ) : null}
        {memberId && !loading ? (
          <>
            <div className="grid grid-cols-2 gap-4 max-narrow:grid-cols-1">
              <fieldset className="grid gap-1">
                <legend className="mb-1 text-caption font-medium">Groups</legend>
                {groups.map((g) => (
                  <label key={g.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={groupIds.includes(g.id)} onChange={(e) => setGroupIds(toggle(groupIds, g.id, e.target.checked))} />
                    {g.name}
                  </label>
                ))}
              </fieldset>
              <fieldset className="grid gap-1">
                <legend className="mb-1 text-caption font-medium">Accounts</legend>
                {accounts.map((a) => (
                  <label key={a.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={accountIds.includes(a.id)} onChange={(e) => setAccountIds(toggle(accountIds, a.id, e.target.checked))} />
                    {a.nickname}
                  </label>
                ))}
              </fieldset>
            </div>
            <Button variant="primary" className="justify-self-start" onClick={() => void save()}>
              Save scope
            </Button>
          </>
        ) : null}
      </PanelBody>
      <PanelFoot>Nothing ticked means the whole workspace. Admins and Owners always see everything.</PanelFoot>
    </Panel>
  );
}
