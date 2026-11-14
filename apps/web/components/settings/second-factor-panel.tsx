"use client";

import { putSecondFactorPolicy } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * Which high-risk actions ask for a second factor here (P2-M2.5, docs/09 §5.3).
 * All are on by default. Admins can switch one on; switching one off takes an
 * Owner and is audited.
 */

const LABELS: Readonly<Record<string, string>> = {
  kill_switch_resume: "Resuming automation after a stop",
  account_disconnect: "Disconnecting a social account",
  company_page_approve: "Approving posts for company or organization pages",
  owner_remove: "Removing an Owner",
  credentials_rotate: "Rotating or revealing credentials",
  video_generate: "Generating video, or media above the cost ceiling",
  comment_delete: "Deleting a comment",
  mcp_server_approve: "Approving an external MCP server for agents",
  mcp_tool_auto: "Letting an agent call a write tool without asking",
};

export function SecondFactorPanel({
  workspaceId,
  policy,
  canEdit,
  isOwner,
}: {
  workspaceId: string;
  policy: Record<string, boolean>;
  canEdit: boolean;
  isOwner: boolean;
}) {
  const router = useRouter();
  const [state, setState] = useState(policy);
  const [error, setError] = useState<string | null>(null);

  async function toggle(action: string, on: boolean) {
    setError(null);
    const r = await settle(putSecondFactorPolicy({ client: browserApi, path: { workspace_id: workspaceId }, body: { changes: { [action]: on } } }));
    if (!r.ok) {
      setError(r.error.code === "forbidden" ? "Only an Owner can switch a protection off." : displayMessage(r.error));
      return;
    }
    setState(r.data.data);
    router.refresh();
  }

  return (
    <Panel aria-labelledby="sf-policy-title">
      <PanelHead title={<span id="sf-policy-title">Second factors</span>} />
      <PanelBody className="grid gap-2">
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        <ul className="grid gap-2" aria-label="Actions that need a second factor">
          {Object.entries(LABELS).map(([action, label]) => {
            const on = state[action] ?? true;
            return (
              <li key={action} className="flex items-center justify-between gap-3 text-sm">
                <span>{label}</span>
                <Switch
                  aria-label={label}
                  checked={on}
                  disabled={!canEdit || (on && !isOwner)}
                  onCheckedChange={(v: boolean) => void toggle(action, v)}
                />
              </li>
            );
          })}
        </ul>
      </PanelBody>
      <PanelFoot>Stopping automation never needs one: stopping is always safe, resuming is a decision.</PanelFoot>
    </Panel>
  );
}
