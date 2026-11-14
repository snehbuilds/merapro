"use client";

import { setSourceGroups, sourceHealth, updateSource, type SourcePatch } from "@mera/sdk";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { GroupDot } from "@/components/mera/group-dot";
import { Button } from "@/components/ui/button";
import { DescriptionList, type DescriptionItem } from "@/components/ui/description-list";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { formErrors } from "@/lib/api/errors";
import type { Source, SourceHealth } from "@/lib/api/models";
import { groupSwatch } from "@/lib/groups";
import {
  SOURCE_ERROR_TEXT,
  SOURCE_FORM_FIELDS,
  displayUrl,
  formatInterval,
  intervalChoices,
  joinKeywords,
  parseKeywords,
  sourceTypeLabel,
  type SourceFormField,
} from "@/lib/sources";
import { GroupPicker, type PickerGroup } from "./group-picker";

/*
 * sources.html detail panel: the selected source's health (why it is failing,
 * whether retries continue, when the next attempt is) and its controls. The
 * list row already carries most fields; GET /sources/{id}/health adds the
 * failure start, HTTP status and whether retries have stopped.
 */

export type DetailMode = "view" | "edit" | "delete";

export interface SourceDetailProps {
  source: Source;
  groups: readonly PickerGroup[];
  workspaceId: string;
  tz: string;
  canEdit: boolean;
  mode: DetailMode;
  onModeChange: (mode: DetailMode) => void;
  /** Busy with a row action (fetch, pause, resume, delete). */
  busy: boolean;
  /** The last fetch queued for this source, if any. */
  queued: boolean;
  onFetch: () => void;
  onStatus: (status: "active" | "paused") => void;
  onDelete: () => void;
  onSaved: (source: Source) => void;
  headingId: string;
}

type HealthState = { kind: "loading" } | { kind: "ok"; health: SourceHealth } | { kind: "error" };

function useHealth(workspaceId: string, source: Source): HealthState {
  // Refetch whenever the source's own state moves (status, failure count, last fetch).
  const version = `${source.id}|${source.status}|${source.consecutive_failures}|${source.last_fetched_at ?? ""}`;
  const [state, setState] = useState<{ version: string; value: HealthState }>({ version, value: { kind: "loading" } });
  if (state.version !== version) setState({ version, value: { kind: "loading" } });
  useEffect(() => {
    let live = true;
    void settle(sourceHealth({ client: browserApi, path: { workspace_id: workspaceId, source_id: source.id } })).then((r) => {
      if (live) setState({ version, value: r.ok ? { kind: "ok", health: r.data } : { kind: "error" } });
    });
    return () => {
      live = false;
    };
  }, [workspaceId, source.id, version]);
  return state.version === version ? state.value : { kind: "loading" };
}

/** "HTTP 403" from the health check, else the stored error, else a plain "Fetching fails". */
function failureLead(source: Source, health: SourceHealth | undefined): string {
  if (health?.last_http_status) return `HTTP ${health.last_http_status}`;
  const err = health?.last_error ?? source.last_error;
  return err ? err : "Fetching fails";
}

function FailingNotice({ source, health, tz }: { source: Source; health: SourceHealth | undefined; tz: string }) {
  const n = health?.consecutive_failures ?? source.consecutive_failures;
  const since = health?.failing_since;
  const lastError = health?.last_error ?? source.last_error;
  const retrying = health?.retrying;
  return (
    <Notice
      tone="danger"
      role="status"
      title={
        <>
          {failureLead(source, health)}
          {since ? (
            <>
              {" since "}
              <Time value={since} tz={tz} format="datetime" className="font-ui text-sm" />
            </>
          ) : null}
          .
        </>
      }
    >
      <span className="mt-[3px] block text-caption">
        {n} consecutive {n === 1 ? "failure" : "failures"}.
        {lastError && health?.last_http_status ? (
          <>
            {" Last error: "}
            <code className="font-mono text-mono-xs">{lastError}</code>.
          </>
        ) : null}{" "}
        {retrying === false
          ? "Retries have stopped after 48 hours of failures. Retry now, or fix the feed and resume it."
          : retrying === true
            ? "Ingestion backs off between attempts and stops retrying after 48 hours of failures."
            : null}
      </span>
    </Notice>
  );
}

function nextAttempt(source: Source, health: HealthState, tz: string): ReactNode {
  if (source.status === "paused") return "Paused";
  if (health.kind === "ok" && !health.health.retrying) return "Retries stopped";
  const at = health.kind === "ok" ? health.health.next_fetch_at : source.next_fetch_at;
  return at ? <Time value={at} tz={tz} format="datetime" /> : "Not scheduled";
}

export function SourceDetail(props: SourceDetailProps) {
  const { source, groups, workspaceId, tz, canEdit, mode, onModeChange, busy, queued, onFetch, onStatus, onDelete, headingId } = props;
  const health = useHealth(workspaceId, source);
  const h = health.kind === "ok" ? health.health : undefined;
  const linked = source.group_ids.map((id) => groups.find((g) => g.id === id)).filter((g): g is PickerGroup => !!g);

  const items: DescriptionItem[] = [
    { term: "Type", detail: sourceTypeLabel(source.source_type) },
    { term: "Feed URL", detail: displayUrl(source.url) || "—", mono: true },
    {
      term: "Feeds",
      detail:
        source.group_ids.length === 0 ? (
          "Whole workspace"
        ) : (
          <span className="inline-flex flex-wrap justify-end gap-x-2 gap-y-1">
            {linked.map((g) => (
              <span key={g.id} className="inline-flex items-center gap-[5px]">
                <GroupDot group={g.id} style={{ background: groupSwatch(g) }} />
                {g.name}
              </span>
            ))}
            {linked.length < source.group_ids.length ? <span>{source.group_ids.length - linked.length} more</span> : null}
          </span>
        ),
    },
    { term: "Every", detail: formatInterval(source.fetch_interval_seconds) },
    {
      term: "Last success",
      detail: (h?.last_success_at ?? source.last_success_at) ? (
        <Time value={(h?.last_success_at ?? source.last_success_at)!} tz={tz} format="datetime" />
      ) : (
        "Never"
      ),
    },
    { term: "Consecutive failures", detail: String(h?.consecutive_failures ?? source.consecutive_failures) },
    { term: "Next attempt", detail: nextAttempt(source, health, tz) },
    { term: "Items / 7d", detail: String(h?.items_7d ?? source.items_7d) },
    ...(h ? [{ term: "Filtered by keywords / 7d", detail: String(h.filtered_7d) }] : []),
    ...(source.include_keywords.length ? [{ term: "Include", detail: joinKeywords(source.include_keywords) }] : []),
    ...(source.exclude_keywords.length ? [{ term: "Exclude", detail: joinKeywords(source.exclude_keywords) }] : []),
    { term: "Weight", detail: source.weight.toFixed(1), mono: true },
  ];

  return (
    <Panel aria-labelledby={headingId} aria-busy={health.kind === "loading"}>
      <PanelHead>
        <h2 className="min-w-0 truncate text-md font-semibold">
          <span id={headingId} tabIndex={-1} className="outline-none">
            {source.name}
          </span>
        </h2>
        <span className="flex-1" />
        <StatusPill kind="source" state={source.status} />
      </PanelHead>
      <PanelBody className="grid gap-3.5">
        {source.status === "failing" ? <FailingNotice source={source} health={h} tz={tz} /> : null}
        {source.status === "paused" ? (
          <Notice title="Paused." icon="pause">
            Nothing is fetched until it is resumed. Items already brought in stay in Discovery.
          </Notice>
        ) : null}
        {health.kind === "error" ? (
          <p className="text-caption text-fg-subtle">Couldn&apos;t load the latest health check; showing the last known state.</p>
        ) : null}

        {mode === "edit" && canEdit ? (
          <EditSourceForm {...props} />
        ) : (
          <>
            <DescriptionList items={items} />
            {queued ? (
              <p className="text-caption text-fg-muted">Fetch queued — runs within a minute.</p>
            ) : null}
            {canEdit ? (
              <div className="flex flex-wrap items-center gap-2">
                {source.status !== "paused" ? (
                  <Button size="sm" onClick={onFetch} disabled={busy}>
                    {source.status === "failing" ? "Retry now" : "Fetch now"}
                  </Button>
                ) : null}
                <Button size="sm" onClick={() => onModeChange("edit")} disabled={busy}>
                  Edit
                </Button>
                {source.status === "paused" ? (
                  <Button size="sm" onClick={() => onStatus("active")} disabled={busy}>
                    Resume source
                  </Button>
                ) : (
                  <Button size="sm" variant="danger" onClick={() => onStatus("paused")} disabled={busy}>
                    Pause source
                  </Button>
                )}
                <span className="flex-1" />
                {mode === "delete" ? null : (
                  <Button size="sm" variant="ghost" onClick={() => onModeChange("delete")} disabled={busy}>
                    Delete
                  </Button>
                )}
              </div>
            ) : null}
            {mode === "delete" && canEdit ? (
              <Notice
                tone="danger"
                title={`Delete ${source.name}?`}
                actions={
                  <>
                    <Button size="sm" variant="danger" onClick={onDelete} disabled={busy}>
                      {busy ? "Deleting…" : `Delete ${source.name}`}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => onModeChange("view")} disabled={busy}>
                      Cancel
                    </Button>
                  </>
                }
              >
                It stops fetching and leaves the source list. Items it already brought in stay in Discovery. Pausing keeps
                it for later instead.
              </Notice>
            ) : null}
          </>
        )}
      </PanelBody>
    </Panel>
  );
}

function sameIds(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && [...a].sort().join() === [...b].sort().join();
}

function EditSourceForm({ source, groups, workspaceId, onModeChange, onSaved }: SourceDetailProps) {
  const [name, setName] = useState(source.name);
  const [url, setUrl] = useState(source.url ?? "");
  const [include, setInclude] = useState(joinKeywords(source.include_keywords));
  const [exclude, setExclude] = useState(joinKeywords(source.exclude_keywords));
  const [interval, setFetchInterval] = useState(source.fetch_interval_seconds);
  const [weight, setWeight] = useState(String(source.weight));
  const [groupIds, setGroupIds] = useState<string[]>(source.group_ids);
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<SourceFormField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setFormError(null);
    const w = Number(weight);
    if (!name.trim()) return setErrors({ name: "Give the source a name." });
    if (weight.trim() === "" || !Number.isFinite(w) || w < 0 || w > 4) return setErrors({ weight: "Weight must be between 0 and 4." });

    const patch: SourcePatch = {};
    if (name.trim() !== source.name) patch.name = name.trim();
    if (url.trim() !== (source.url ?? "")) patch.url = url.trim();
    const inc = parseKeywords(include);
    const exc = parseKeywords(exclude);
    if (joinKeywords(inc) !== joinKeywords(source.include_keywords)) patch.include_keywords = inc;
    if (joinKeywords(exc) !== joinKeywords(source.exclude_keywords)) patch.exclude_keywords = exc;
    if (interval !== source.fetch_interval_seconds) patch.fetch_interval_seconds = interval;
    if (w !== source.weight) patch.weight = w;
    const groupsChanged = !sameIds(groupIds, source.group_ids);

    setPending(true);
    let latest = source;
    if (Object.keys(patch).length > 0) {
      const r = await settle(
        updateSource({ client: browserApi, path: { workspace_id: workspaceId, source_id: source.id }, body: patch }),
      );
      if (!r.ok) {
        setPending(false);
        const split = formErrors(r.error, SOURCE_FORM_FIELDS);
        setErrors(split.fields);
        if (split.form.length) setFormError(SOURCE_ERROR_TEXT[r.error.code] ?? split.form.join(" "));
        return;
      }
      latest = r.data;
    }
    if (groupsChanged) {
      const r = await settle(
        setSourceGroups({ client: browserApi, path: { workspace_id: workspaceId, source_id: source.id }, body: { group_ids: groupIds } }),
      );
      if (!r.ok) {
        setPending(false);
        if (latest !== source) onSaved(latest);
        const split = formErrors(r.error, ["group_ids"] as const);
        if (split.fields.group_ids) setErrors({ group_ids: split.fields.group_ids });
        if (split.form.length) setFormError(SOURCE_ERROR_TEXT[r.error.code] ?? split.form.join(" "));
        return;
      }
      latest = r.data;
    }
    setPending(false);
    onSaved(latest);
    onModeChange("view");
  }

  return (
    <form onSubmit={save} noValidate aria-label={`Edit ${source.name}`} className="grid gap-3.5">
      {formError ? (
        <Notice tone="danger" role="alert">
          {formError}
        </Notice>
      ) : null}
      <Field label="Name" error={errors.name}>
        <Input value={name} maxLength={200} disabled={pending} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Feed URL" error={errors.url}>
        <Input type="url" inputMode="url" value={url} disabled={pending} onChange={(e) => setUrl(e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-2.5 max-narrow:grid-cols-1">
        <Field label="Include keywords" error={errors.include_keywords} hint="Comma separated.">
          <Input value={include} disabled={pending} onChange={(e) => setInclude(e.target.value)} />
        </Field>
        <Field label="Exclude" error={errors.exclude_keywords} hint="Comma separated.">
          <Input value={exclude} disabled={pending} onChange={(e) => setExclude(e.target.value)} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <Field label="Fetch every" error={errors.fetch_interval_seconds}>
          <Select value={interval} disabled={pending} onChange={(e) => setFetchInterval(Number(e.target.value))}>
            {intervalChoices(source.fetch_interval_seconds).map((s) => (
              <option key={s} value={s}>
                {formatInterval(s)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Weight" error={errors.weight} hint="0 to 4. Scales this source's relevance.">
          <Input inputMode="decimal" value={weight} disabled={pending} onChange={(e) => setWeight(e.target.value)} />
        </Field>
      </div>
      <GroupPicker groups={groups} value={groupIds} onChange={setGroupIds} disabled={pending} error={errors.group_ids} />
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={pending}>
          {pending ? "Saving…" : "Save source"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onModeChange("view")} disabled={pending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
