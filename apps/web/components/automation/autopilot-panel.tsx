"use client";

import { getWorkspace, updateWorkspace } from "@mera/sdk";
import { useEffect, useState } from "react";
import { useSecondFactor } from "@/components/mera/second-factor";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { AUTOPILOT_COPY, DEFAULT_THRESHOLD, type AutopilotMode, parseThreshold } from "@/lib/autopilot";

/*
 * Autopilot mode for the whole workspace (P3-M3, docs/14). Groups and accounts can set
 * their own mode; the strictest set anywhere wins, and a company account stays Manual
 * unless its own setting opts in. Turning it up takes an Admin (Full: an Owner) and a
 * second factor; the API also refuses until approvals have earned it.
 */
export function AutopilotPanel({ workspaceId, canChange }: { workspaceId: string; canChange: boolean }) {
  const { withSecondFactor } = useSecondFactor();
  const [mode, setMode] = useState<AutopilotMode>("manual");
  const [saved, setSaved] = useState<AutopilotMode>("manual");
  const [threshold, setThreshold] = useState(String(DEFAULT_THRESHOLD));
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ tone: "info" | "danger"; text: string } | null>(null);

  useEffect(() => {
    let live = true;
    void settle(getWorkspace({ client: browserApi, path: { workspace_id: workspaceId } })).then((r) => {
      if (!live || !r.ok) return;
      const s = ((r.data as { settings?: Record<string, unknown> }).settings ?? {}) as Record<string, unknown>;
      const m = (s.autopilot_mode as AutopilotMode | undefined) ?? "manual";
      setMode(m);
      setSaved(m);
      if (typeof s.assisted_threshold === "number") setThreshold(String(s.assisted_threshold));
      setLoaded(true);
    });
    return () => {
      live = false;
    };
  }, [workspaceId]);

  const parsed = parseThreshold(threshold);

  async function save() {
    if (mode === "assisted" && parsed === null) return;
    setBusy(true);
    setFlash(null);
    const settings: Record<string, unknown> = { autopilot_mode: mode };
    if (mode === "assisted") settings.assisted_threshold = parsed;
    const r = await withSecondFactor((headers) =>
      settle(updateWorkspace({ client: browserApi, headers, path: { workspace_id: workspaceId }, body: { settings } })),
    );
    setBusy(false);
    if (!r.ok) {
      setFlash({ tone: "danger", text: displayMessage(r.error) });
      return;
    }
    setSaved(mode);
    setFlash({ tone: "info", text: `Autopilot is now ${AUTOPILOT_COPY[mode].label}.` });
  }

  return (
    <Panel>
      <PanelHead title="Mode" />
      <PanelBody className="grid gap-3">
        <SegmentedControl<AutopilotMode>
          aria-label="Autopilot mode"
          value={mode}
          onValueChange={setMode}
          options={(["manual", "assisted", "full"] as const).map((v) => ({
            value: v,
            label: AUTOPILOT_COPY[v].label,
            disabled: !canChange || !loaded,
          }))}
        />
        <p className="text-sm text-fg-muted">{AUTOPILOT_COPY[mode].help}</p>
        {mode === "assisted" ? (
          <Field
            label="Publish without a person at or above this Editor confidence"
            hint="Between 0.50 and 0.99. Below it, the post waits for approval as usual."
            error={parsed === null ? "Enter a number between 0.50 and 0.99." : undefined}
          >
            <Input
              inputMode="decimal"
              className="w-28"
              value={threshold}
              disabled={!canChange}
              onChange={(e) => setThreshold(e.target.value)}
            />
          </Field>
        ) : null}
        {mode !== "manual" ? (
          <Notice tone="warn">
            Mode is earned per account: at least 30 approvals with under 10% edited, and for Assisted a confidence score
            that separates good drafts from bad. Until then the API refuses and tells you what is missing. Any
            published post can be stopped from the Telegram or WhatsApp notice.
          </Notice>
        ) : null}
        <div role="status" aria-live="polite" className="empty:hidden">
          {flash ? <Notice tone={flash.tone === "info" ? "info" : "danger"}>{flash.text}</Notice> : null}
        </div>
        {canChange ? (
          <div>
            <Button
              variant="primary"
              size="sm"
              disabled={busy || !loaded || (mode === saved && mode !== "assisted") || (mode === "assisted" && parsed === null)}
              onClick={() => void save()}
            >
              {busy ? "Saving…" : "Save mode"}
            </Button>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">Only an Admin can change the mode.</p>
        )}
      </PanelBody>
    </Panel>
  );
}
