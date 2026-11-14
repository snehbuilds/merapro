"use client";

import { confirmPending, declinePending, type PendingOut } from "@mera/sdk";
import { useState } from "react";
import { useSecondFactor } from "@/components/mera/second-factor";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * An MCP client asked for something that needs a second factor (docs/05 §1.2). The code
 * is entered here, in Mera, never in the chat. Confirming runs the action as you, with
 * every check it would have anywhere else; the client then picks up the result.
 */
export function ConfirmAction({ workspace, pending }: { workspace: { id: string; timezone: string }; pending: PendingOut }) {
  const { withSecondFactor } = useSecondFactor();
  const [state, setState] = useState(pending.state);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const path = { workspace_id: workspace.id, pending_id: pending.id };

  async function confirm() {
    setBusy(true);
    setError(null);
    const r = await withSecondFactor((headers) => settle(confirmPending({ client: browserApi, headers, path })));
    setBusy(false);
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    setState(r.data.state);
  }

  async function decline() {
    setBusy(true);
    const r = await settle(declinePending({ client: browserApi, path }));
    setBusy(false);
    if (r.ok) setState(r.data.state);
  }

  return (
    <Panel aria-labelledby="confirm-title">
      <PanelHead title={<span id="confirm-title">{pending.summary}</span>} />
      <PanelBody className="grid gap-3">
        <p className="text-sm text-fg-muted">
          Requested through <span className="font-mono">{pending.tool}</span>. It expires{" "}
          <Time value={pending.expires_at} tz={workspace.timezone} format="time" />.
        </p>
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        <div role="status" aria-live="polite">
          {state === "done" ? <Notice tone="info" icon="check">Done. Go back to your MCP client; it will pick up the result.</Notice> : null}
          {state === "declined" ? <Notice>Declined. Nothing was done.</Notice> : null}
          {state === "expired" ? <Notice tone="warn">This request expired. Ask your client to try again.</Notice> : null}
          {state === "failed" ? <Notice tone="danger">It could not be done.</Notice> : null}
        </div>
      </PanelBody>
      {state === "pending" ? (
        <PanelFoot className="flex flex-wrap gap-2">
          <Button variant="danger" disabled={busy} onClick={() => void confirm()}>
            {busy ? "Confirming…" : "Confirm with second factor"}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => void decline()}>
            Decline
          </Button>
        </PanelFoot>
      ) : null}
    </Panel>
  );
}
