"use client";

import { useSecondFactor } from "@/components/mera/second-factor";
import { AutopilotPanel } from "./autopilot-panel";
import { PoliciesPanel } from "./policies-panel";
import { killSwitch, type AutomationOut, type PolicyOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { AccountChip } from "@/components/mera/account-chip";
import { GroupDot } from "@/components/mera/group-dot";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Time } from "@/components/ui/time";
import { platformIcon, platformLabel } from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { SCREENS } from "@/lib/nav";

/*
 * Automation (automation.html, M6.12, docs/12 §4; P3-M3). The autopilot mode (Manual,
 * Assisted, Full) is earned per account and guarded by a second factor. The kill switch is live at three scopes; engaged anywhere above an account, it
 * stops that account. Stopping is instant (Approvers and up); resuming is an Admin's.
 */

type Scope = { scope_type: "workspace" | "account_group" | "social_account"; scope_id: string | null; label: string };

export interface AutomationScreenProps {
  workspace: { id: string; slug: string; timezone: string; archived: boolean; role: string };
  states: AutomationOut[];
  groups: { id: string; name: string }[];
  accounts: { id: string; nickname: string; handle: string; platform: string; avatar_url: string | null }[];
  /** Approval policies and the people who can approve (P2-M2.8); absent if unloadable. */
  policies?: PolicyOut[];
  approvers?: { id: string; name: string }[];
}

export function AutomationScreen({ workspace, states, groups, accounts, policies, approvers }: AutomationScreenProps) {
  const router = useRouter();
  const { withSecondFactor } = useSecondFactor();
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const canStop = workspace.role !== "viewer";
  const canResume = workspace.role === "owner" || workspace.role === "admin";
  const engaged = (s: Scope) =>
    states.find((a) => a.scope_type === s.scope_type && (a.scope_id ?? null) === s.scope_id && a.kill_switch_engaged) ?? null;
  const wsScope: Scope = { scope_type: "workspace", scope_id: null, label: "Whole workspace" };
  const wsStopped = engaged(wsScope);

  async function set(s: Scope, on: boolean) {
    setBusy(`${s.scope_type}:${s.scope_id}`);
    setFlash(null);
    const r = await withSecondFactor((headers) =>
      settle(
        killSwitch({
          client: browserApi,
          headers,
          path: { workspace_id: workspace.id },
          body: { scope_type: s.scope_type, scope_id: s.scope_id, engaged: on, reason: on ? "Stopped from Automation" : null },
        }),
      ),
    );
    setBusy(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: displayMessage(r.error) });
      return;
    }
    setFlash({ tone: "info", text: on ? `Stopped: ${s.label}. Nothing publishes there until an Admin resumes it.` : `Resumed: ${s.label}.` });
    router.refresh();
  }

  function row(scope: Scope, children: ReactNode) {
    const on = engaged(scope);
    const key = `${scope.scope_type}:${scope.scope_id}`;
    return (
      <li key={key} className="flex flex-wrap items-center gap-2.5 px-3.5 py-2.5 text-sm">
        {children}
        <span className="flex-1" />
        {on ? (
          <>
            <StatusPill tone="danger" shape="diamond" label="Stopped" />
            <span className="text-caption text-fg-subtle">
              {on.kill_switch_at ? <Time value={on.kill_switch_at} tz={workspace.timezone} format="datetime" /> : null}
              {on.kill_switch_reason ? ` · ${on.kill_switch_reason}` : ""}
            </span>
            <Button size="sm" disabled={!canResume || busy !== null || (wsStopped !== null && scope.scope_type !== "workspace")} onClick={() => void set(scope, false)}>
              Resume
            </Button>
          </>
        ) : (
          <>
            <StatusPill tone="ok" label={wsStopped && scope.scope_type !== "workspace" ? "Held by workspace stop" : "Running"} />
            <Button size="sm" variant="danger" disabled={!canStop || busy === key || workspace.archived} onClick={() => void set(scope, true)}>
              Stop
            </Button>
          </>
        )}
      </li>
    );
  }

  return (
    <>
      <Topbar title={SCREENS.automation.title} sub={wsStopped ? "Workspace stopped" : "Running"} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div role="status" aria-live="polite" className="empty:hidden">
          {flash?.tone === "info" ? <Notice tone="info" icon="check">{flash.text}</Notice> : null}
        </div>
        {flash?.tone === "danger" ? <Notice tone="danger" role="alert">{flash.text}</Notice> : null}
        {!canResume ? (
          <Notice tone="info">You can stop automation at any scope you can reach. Resuming needs an Admin.</Notice>
        ) : null}

        <AutopilotPanel workspaceId={workspace.id} canChange={canResume && !workspace.archived} />

        <Panel>
          <PanelHead title="Kill switch" />
          <ul className="divide-y divide-border">
            {row(
              wsScope,
              <>
                <span className="font-semibold">Whole workspace</span>
                <span className="text-fg-muted">stops every group and account</span>
              </>,
            )}
          </ul>
        </Panel>

        <Panel>
          <PanelHead title="Groups" />
          <ul className="divide-y divide-border">
            {groups.map((g) =>
              row(
                { scope_type: "account_group", scope_id: g.id, label: g.name },
                <>
                  <GroupDot group={g.id} />
                  <span className="font-medium">{g.name}</span>
                </>,
              ),
            )}
            {groups.length === 0 ? <li className="px-3.5 py-2.5 text-sm text-fg-subtle">No groups yet.</li> : null}
          </ul>
        </Panel>

        <Panel>
          <PanelHead title="Accounts" />
          <ul className="divide-y divide-border">
            {accounts.map((a) =>
              row(
                { scope_type: "social_account", scope_id: a.id, label: a.nickname },
                <AccountChip
                  size="lg"
                  account={{
                    platform: platformIcon(a.platform as never),
                    name: a.nickname,
                    handle: a.handle,
                    avatar_url: a.avatar_url,
                    platformLabel: platformLabel(a.platform as never),
                  }}
                />,
              ),
            )}
          </ul>
        </Panel>
        {policies ? (
          <PoliciesPanel
            workspaceId={workspace.id}
            policies={policies}
            groups={groups}
            accounts={accounts}
            approvers={approvers ?? []}
            canEdit={(workspace.role === "owner" || workspace.role === "admin") && !workspace.archived}
          />
        ) : null}
      </main>
    </>
  );
}
