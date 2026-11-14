"use client";

import { updateGroup, type GroupOut, type StaggerMode } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import {
  GROUP_ERROR_TEXT,
  STAGGER_MODES,
  STAGGER_MODE_LABEL,
  minutesToSeconds,
  secondsToMinutes,
  staggerBlock,
  staggerBoundsError,
} from "@/lib/groups";

/*
 * groups.html "Publishing": how a post set spreads across the group's
 * accounts (ADR-0012 §5). With two or more accounts on one platform, "All at
 * once" is a blocking guardrail (docs/08 §3.3): the option is disabled with
 * the reason, and if the API refuses with `stagger_required` anyway (e.g. the
 * membership changed in another tab), its message shows next to the control.
 */

export interface StaggerFormState {
  /** Why "All at once" is unavailable: the API's message once it has said so, else the client's prediction. */
  blockedReason: string | null;
  /** "All at once" is selectable. */
  noneEnabled: boolean;
}

export function staggerFormState(group: Pick<GroupOut, "members">, apiMessage: string | null): StaggerFormState {
  const predicted = staggerBlock(group.members);
  const blockedReason = apiMessage ?? predicted;
  return { blockedReason, noneEnabled: blockedReason === null };
}

export function StaggerPanel({ group, workspaceId, canEdit }: { group: GroupOut; workspaceId: string; canEdit: boolean }) {
  const router = useRouter();
  const [mode, setMode] = useState<StaggerMode>(group.stagger_mode);
  const [min, setMin] = useState(secondsToMinutes(group.stagger_min_seconds));
  const [max, setMax] = useState(secondsToMinutes(group.stagger_max_seconds));
  const [apiStaggerMessage, setApiStaggerMessage] = useState<string | null>(null);
  const [boundsError, setBoundsError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);

  const { blockedReason, noneEnabled } = staggerFormState(group, apiStaggerMessage);
  const staggered = mode !== "none";
  const changed =
    mode !== group.stagger_mode ||
    (staggered &&
      (minutesToSeconds(min) !== group.stagger_min_seconds || minutesToSeconds(max) !== group.stagger_max_seconds));

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    setFormError(null);
    setBoundsError(null);
    const lo = minutesToSeconds(min);
    const hi = minutesToSeconds(max);
    if (staggered) {
      const bounds = staggerBoundsError(lo, hi);
      if (bounds) {
        setBoundsError(bounds);
        return;
      }
    }
    setPending(true);
    const r = await settle(
      updateGroup({
        client: browserApi,
        path: { workspace_id: workspaceId, group_id: group.id },
        body: staggered
          ? { stagger_mode: mode, stagger_min_seconds: lo, stagger_max_seconds: hi }
          : { stagger_mode: mode },
      }),
    );
    setPending(false);
    if (!r.ok) {
      if (r.error.code === "stagger_required") {
        setApiStaggerMessage(r.error.fieldErrors.stagger_mode ?? r.error.message);
        setMode(group.stagger_mode === "none" ? "random_spread" : group.stagger_mode);
        return;
      }
      if (r.error.code === "invalid_stagger_bounds") {
        setBoundsError(r.error.fieldErrors.stagger_min_seconds ?? r.error.message);
        return;
      }
      setFormError(GROUP_ERROR_TEXT[r.error.code] ?? displayMessage(r.error));
      return;
    }
    setApiStaggerMessage(null);
    setSaved(true);
    router.refresh();
  }

  const disabled = !canEdit || pending;
  // The reason sits next to the control: as an error once the API has refused,
  // as a hint while it is the client's prediction.
  const modeError = apiStaggerMessage ?? undefined;
  const modeHint =
    !apiStaggerMessage && blockedReason
      ? blockedReason
      : mode === "none"
        ? "Every account publishes at the same moment."
        : "Offsets follow the member order above, computed once at scheduling.";

  return (
    <Panel>
      <PanelHead title="Publishing" />
      <PanelBody>
        <form onSubmit={save} noValidate className="grid gap-3.5" aria-label="Stagger settings">
          {formError ? (
            <Notice tone="danger" role="alert">
              {formError}
            </Notice>
          ) : null}
          <Field label="Stagger mode" hint={modeError ? undefined : modeHint} error={modeError}>
            <Select
              value={mode}
              disabled={disabled}
              onChange={(e) => {
                setMode(e.target.value as StaggerMode);
                setSaved(false);
              }}
            >
              {STAGGER_MODES.map((m) => (
                <option key={m} value={m} disabled={m === "none" && !noneEnabled}>
                  {m === "none" && !noneEnabled ? `${STAGGER_MODE_LABEL[m]} (blocked)` : STAGGER_MODE_LABEL[m]}
                </option>
              ))}
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-2.5">
            <Field label="Minimum (minutes)" error={boundsError ?? undefined}>
              <Input inputMode="decimal" value={min} disabled={disabled || !staggered} onChange={(e) => setMin(e.target.value)} />
            </Field>
            <Field label="Maximum (minutes)">
              <Input inputMode="decimal" value={max} disabled={disabled || !staggered} onChange={(e) => setMax(e.target.value)} />
            </Field>
          </div>
          {canEdit ? (
            <div className="flex items-center gap-2.5">
              <Button type="submit" size="sm" disabled={pending || !changed}>
                {pending ? "Saving…" : "Save publishing"}
              </Button>
              {saved ? (
                <span role="status" className="text-caption text-fg-subtle">
                  Saved.
                </span>
              ) : null}
            </div>
          ) : null}
        </form>
      </PanelBody>
    </Panel>
  );
}
