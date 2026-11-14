"use client";

import { setMediaLimits, type SpendOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { usd } from "@/components/composer/media-panel";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * Media generation spend (P2-M5): this calendar month's cost against the budget,
 * and the per-request ceiling above which a second factor is asked for. The
 * spend is the provider-reported cost of every job, kept whether or not its image
 * was used, plus the estimate of jobs still running.
 */

const STATE_LABEL: Readonly<Record<string, string>> = {
  succeeded: "done",
  failed: "failed",
  cancelled: "cancelled",
  timed_out: "timed out",
  queued: "queued",
  running: "running",
};

export function MediaSpendPanel({ workspaceId, spend, canEdit }: { workspaceId: string; spend: SpendOut; canEdit: boolean }) {
  const id = useId();
  const router = useRouter();
  const [ceiling, setCeiling] = useState(String(Number(spend.request_ceiling_usd)));
  const [budget, setBudget] = useState(String(Number(spend.monthly_budget_usd)));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const used = Number(spend.month_spent_usd);
  const cap = Number(spend.monthly_budget_usd);
  const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 100;
  const jobs = Object.entries(spend.jobs);

  async function save() {
    setBusy(true);
    setNote(null);
    const r = await settle(
      setMediaLimits({
        client: browserApi,
        path: { workspace_id: workspaceId },
        body: { request_ceiling_usd: ceiling, monthly_budget_usd: budget },
      }),
    );
    setBusy(false);
    if (!r.ok) {
      setNote({ tone: "danger", text: displayMessage(r.error) });
      return;
    }
    setNote({ tone: "info", text: "Limits saved." });
    router.refresh();
  }

  return (
    <Panel aria-labelledby={`${id}-title`}>
      <PanelHead title={<span id={`${id}-title`}>Media generation</span>} />
      <PanelBody className="grid gap-3">
        {spend.provider ? null : <Notice>No image provider is configured on this deployment, so nothing can be generated.</Notice>}
        <div className="grid gap-1.5">
          <p className="text-sm">
            <span className="font-semibold">{usd(spend.month_spent_usd)}</span> of {usd(spend.monthly_budget_usd)} this month
          </p>
          <div
            role="meter"
            aria-label="Media budget used this month"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            className="h-2 overflow-hidden rounded-full bg-surface-sunken"
          >
            <div className={pct >= 90 ? "h-full bg-danger" : "h-full bg-accent"} style={{ width: `${pct}%` }} />
          </div>
          <p className="text-caption text-fg-subtle">
            {jobs.length
              ? jobs.map(([state, n]) => `${n} ${STATE_LABEL[state] ?? state}`).join(" · ")
              : "No images generated this month."}
            {spend.video_enabled ? "" : " · Video is off."}
          </p>
        </div>
        {note ? (
          <Notice tone={note.tone} role={note.tone === "danger" ? "alert" : "status"}>
            {note.text}
          </Notice>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <div className="grid gap-1">
            <Label htmlFor={`${id}-ceiling`}>Per-request ceiling (USD)</Label>
            <Input
              id={`${id}-ceiling`}
              inputMode="decimal"
              value={ceiling}
              disabled={!canEdit || busy}
              onChange={(e) => setCeiling(e.target.value)}
              className="w-32"
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor={`${id}-budget`}>Monthly budget (USD)</Label>
            <Input
              id={`${id}-budget`}
              inputMode="decimal"
              value={budget}
              disabled={!canEdit || busy}
              onChange={(e) => setBudget(e.target.value)}
              className="w-32"
            />
          </div>
        </div>
        <p className="text-caption text-fg-subtle">
          A request above the ceiling needs a second factor. Over the monthly budget, generation stops until next month or
          until the budget is raised.
        </p>
      </PanelBody>
      {canEdit ? (
        <PanelFoot>
          <Button size="sm" variant="primary" disabled={busy || !/^\d+(\.\d+)?$/.test(ceiling) || !/^\d+(\.\d+)?$/.test(budget)} onClick={() => void save()}>
            {busy ? "Saving…" : "Save limits"}
          </Button>
        </PanelFoot>
      ) : null}
    </Panel>
  );
}
