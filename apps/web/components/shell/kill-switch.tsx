"use client";

import { useSecondFactor } from "@/components/mera/second-factor";
import { killSwitch, listAutomation } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { Icon } from "@/components/icons";
import { useShell } from "@/components/shell/shell-provider";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { isAdminRole } from "@/lib/workspace";

/*
 * mera.css .killswitch — the one action that must never be hunted for. Loud on
 * purpose, isolated on purpose: nothing else lives at the bottom of the rail.
 *
 * Live since Sprint 8 (M6.12): stops the whole workspace. While stopped, no
 * approval can be given and nothing publishes (assert_publishable check 9).
 * Stopping is always safe, so Approvers and up may; resuming is an Admin's.
 * Group and account stops live on the Automation screen.
 */
export function KillSwitch() {
  const { withSecondFactor } = useSecondFactor();
  const { workspace } = useShell();
  const router = useRouter();
  const id = useId();
  const [stopped, setStopped] = useState<boolean | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canStop = workspace.role !== "viewer";
  const canResume = isAdminRole(workspace.role);

  useEffect(() => {
    let live = true;
    void settle(listAutomation({ client: browserApi, path: { workspace_id: workspace.id } })).then((r) => {
      if (!live || !r.ok) return;
      setStopped(r.data.data.some((a) => a.scope_type === "workspace" && a.kill_switch_engaged));
    });
    return () => {
      live = false;
    };
  }, [workspace.id]);

  async function set(engaged: boolean) {
    setBusy(true);
    setError(null);
    const r = await withSecondFactor((headers) =>
      settle(
        killSwitch({
          client: browserApi,
          headers,
          path: { workspace_id: workspace.id },
          body: { scope_type: "workspace", engaged, reason: engaged ? "Stopped from the rail" : null },
        }),
      ),
    );
    setBusy(false);
    setConfirming(false);
    if (!r.ok && r.error.code !== "already_stopped" && r.error.code !== "not_stopped") {
      setError(displayMessage(r.error));
      return;
    }
    setStopped(engaged);
    router.refresh();
  }

  if (stopped) {
    return (
      <div data-slot="kill-switch" className="grid gap-1.5 rounded-md border border-kill-border bg-kill-bg p-2.5 text-caption text-kill-fg">
        <p className="flex items-center gap-1.5 font-label font-semibold tracking-[0.08em] uppercase">
          <Icon name="power" />
          Automation stopped
        </p>
        <p>No approvals are taken and nothing publishes until an Admin resumes.</p>
        {canResume ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void set(false)}
            className="h-7 rounded-sm border border-kill-border px-2 font-semibold hover:bg-kill-bg-hover disabled:opacity-55"
          >
            {busy ? "Resuming…" : "Resume automation"}
          </button>
        ) : null}
        {error ? <p role="alert">{error}</p> : null}
      </div>
    );
  }

  if (confirming) {
    return (
      <div data-slot="kill-switch" role="alertdialog" aria-labelledby={`${id}-q`} className="grid gap-1.5 rounded-md border border-kill-border bg-kill-bg p-2.5 text-caption text-kill-fg">
        <p id={`${id}-q`} className="font-semibold">
          Stop all automation in {workspace.name}? Approvals and publishing halt for every account.
        </p>
        <div className="flex gap-1.5">
          <button
            type="button"
            autoFocus
            disabled={busy}
            onClick={() => void set(true)}
            className="h-7 flex-1 rounded-sm bg-kill-fg px-2 font-semibold text-kill-bg disabled:opacity-55"
          >
            {busy ? "Stopping…" : "Stop now"}
          </button>
          <button type="button" onClick={() => setConfirming(false)} className="h-7 rounded-sm border border-kill-border px-2">
            Cancel
          </button>
        </div>
        {error ? <p role="alert">{error}</p> : null}
      </div>
    );
  }

  return (
    <button
      type="button"
      data-slot="kill-switch"
      disabled={!canStop || stopped === null}
      onClick={() => setConfirming(true)}
      className="flex h-9 w-full items-center gap-2 rounded-md border border-kill-border bg-kill-bg px-3 font-label text-caption font-semibold tracking-[0.08em] text-kill-fg uppercase hover:border-kill-border-hover hover:bg-kill-bg-hover hover:text-kill-fg-hover disabled:opacity-55"
    >
      <Icon name="power" />
      Stop all automation
    </button>
  );
}
