"use client";

import { groupSettings } from "@mera/sdk";
import { useEffect, useState } from "react";
import { ResolvedSettingRow, ResolvedSettingsTable } from "@/components/mera/resolved-setting-row";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { SkeletonRows } from "@/components/ui/skeleton";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { resolvedRows, type ResolvedRow } from "@/lib/level-settings";

/*
 * groups.html "Resolved settings": each effective value for this group and
 * the level it came from (GET account-groups/{id}/settings). `version` changes
 * whenever the group does, which refetches.
 */
export function ResolvedSettingsPanel({
  workspaceId,
  groupId,
  version,
}: {
  workspaceId: string;
  groupId: string;
  version: string;
}) {
  const [state, setState] = useState<{ key: string; rows?: ResolvedRow[]; error?: string }>({ key: "" });
  const key = `${groupId}:${version}`;

  useEffect(() => {
    let live = true;
    settle(groupSettings({ client: browserApi, path: { workspace_id: workspaceId, group_id: groupId } })).then((r) => {
      if (!live) return;
      setState(r.ok ? { key, rows: resolvedRows(r.data.data) } : { key, error: displayMessage(r.error) });
    });
    return () => {
      live = false;
    };
  }, [workspaceId, groupId, key]);

  const loading = state.key !== key && !state.rows;
  return (
    <Panel>
      <PanelHead title="Resolved settings" actions={<span className="text-caption text-fg-subtle">and where each came from</span>} />
      <PanelBody flush>
        {state.error ? (
          <p role="alert" className="px-3.5 py-3 text-caption text-danger">
            Couldn&apos;t load resolved settings: {state.error}
          </p>
        ) : loading || !state.rows ? (
          <SkeletonRows rows={4} label="Loading resolved settings" />
        ) : (
          <ResolvedSettingsTable caption="Resolved settings for this group">
            {state.rows.map((r) => (
              <ResolvedSettingRow key={r.key} setting={r.label} value={r.value} source={r.source} rule={r.rule} />
            ))}
          </ResolvedSettingsTable>
        )}
      </PanelBody>
      <PanelFoot>
        Approval policy and autopilot mode resolve to the <strong>strictest</strong> applicable value, not the most
        specific, so adding a locked-down account to a permissive group never quietly loosens it.
      </PanelFoot>
    </Panel>
  );
}
