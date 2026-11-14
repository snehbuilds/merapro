"use client";

import { deleteSource, fetchSource, updateSource, type GroupOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { DataTable, dataTableColumns } from "@/components/mera/data-table";
import { GroupDot } from "@/components/mera/group-dot";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage, type ApiError } from "@/lib/api/errors";
import type { Source } from "@/lib/api/models";
import { groupSwatch } from "@/lib/groups";
import { SCREENS } from "@/lib/nav";
import {
  ALL_SOURCES,
  FETCH_QUEUED_TEXT,
  SOURCE_ERROR_TEXT,
  displayUrl,
  filterSources,
  formatInterval,
  initialSourceId,
  sourceTypeLabel,
  sourcesSummary,
  type SourceFilter,
} from "@/lib/sources";
import { AddSourcePanel, ADD_SOURCE_ID } from "./add-source-panel";
import { RowMenu } from "./row-menu";
import { SourceDetail, type DetailMode } from "./source-detail";

/*
 * Sources (sources.html): every feed, which groups it feeds, whether it is
 * healthy, and how much it brings in; the selected source's health; and the
 * add form. Editors and up configure sources (CONFIGURE_CONTENT); a
 * group-scoped editor only those feeding their groups, which the API
 * enforces. Everyone else reads.
 */

export interface SourcesScreenProps {
  workspace: { id: string; slug: string; name: string; timezone: string; archived: boolean };
  sources: Source[];
  groups: GroupOut[];
  /** Editor+ (CONFIGURE_CONTENT). */
  canManage: boolean;
  /** ?source=<id>: preselect this source. */
  initialSourceId?: string;
}

type RowAction = "select" | "edit" | "fetch" | "pause" | "resume" | "delete";

interface GroupTag {
  id: string;
  name: string;
  swatch: string;
}

interface SourceRow {
  source: Source;
  groups: GroupTag[];
  selected: boolean;
  canEdit: boolean;
  busy: boolean;
  onAction: (action: RowAction, id: string) => void;
}

const STATUS_RANK = { failing: 2, paused: 1, active: 0 } as const;

const col = dataTableColumns<SourceRow>();

const columns = [
  col.accessor((r) => r.source.name, {
    id: "source",
    header: "Source",
    cell: ({ row }) => {
      const { source, selected, onAction } = row.original;
      return (
        <div className="grid min-w-0 justify-items-start gap-0.5">
          <button
            type="button"
            aria-pressed={selected}
            aria-label={`Details for ${source.name}`}
            onClick={() => onAction("select", source.id)}
            className="text-left font-semibold text-fg underline-offset-2 hover:underline aria-pressed:text-accent"
          >
            {source.name}
          </button>
          <span className="max-w-[38ch] truncate font-mono text-mono-xs text-fg-subtle">{displayUrl(source.url)}</span>
        </div>
      );
    },
  }),
  col.accessor((r) => sourceTypeLabel(r.source.source_type), {
    id: "type",
    header: "Type",
    enableSorting: false,
    cell: ({ getValue }) => <span className="text-sm text-fg-muted">{getValue()}</span>,
  }),
  col.display({
    id: "feeds",
    header: "Feeds",
    cell: ({ row }) => {
      const { source, groups } = row.original;
      if (source.group_ids.length === 0) return <span className="text-sm text-fg-subtle">Workspace</span>;
      const hidden = source.group_ids.length - groups.length;
      return (
        <span className="flex flex-wrap items-center gap-x-[7px] gap-y-1">
          {groups.map((g) => (
            <span key={g.id} className="inline-flex items-center gap-[5px] text-sm">
              <GroupDot group={g.id} style={{ background: g.swatch }} />
              {g.name}
            </span>
          ))}
          {hidden > 0 ? <span className="text-sm text-fg-subtle">{hidden} more</span> : null}
        </span>
      );
    },
  }),
  col.accessor((r) => STATUS_RANK[r.source.status], {
    id: "status",
    header: "Status",
    cell: ({ row }) => <StatusPill kind="source" state={row.original.source.status} />,
  }),
  col.accessor((r) => r.source.fetch_interval_seconds, {
    id: "every",
    header: "Every",
    enableSorting: false,
    meta: { align: "end" },
    cell: ({ getValue }) => <span className="text-sm">{formatInterval(getValue())}</span>,
  }),
  col.accessor((r) => r.source.items_7d, {
    id: "items",
    header: "Items / 7d",
    meta: { align: "end" },
  }),
  col.display({
    id: "drafted",
    header: "Drafted",
    meta: { align: "end" },
    // Post sets arrive with drafting (M5): there is nothing to count yet.
    cell: () => (
      <span className="text-fg-subtle">
        <span aria-hidden="true">—</span>
        <span className="sr-only">Not tracked until drafting arrives</span>
      </span>
    ),
  }),
  col.display({
    id: "actions",
    header: () => <span className="sr-only">Actions</span>,
    meta: { align: "end" },
    cell: ({ row }) => {
      const { source, canEdit, busy, onAction } = row.original;
      if (!canEdit) return null;
      const paused = source.status === "paused";
      return (
        <span className="inline-flex items-center justify-end gap-1">
          {source.status === "failing" ? (
            <Button size="sm" onClick={() => onAction("fetch", source.id)} disabled={busy} aria-label={`Retry ${source.name}`}>
              Retry
            </Button>
          ) : null}
          {paused ? (
            <Button size="sm" onClick={() => onAction("resume", source.id)} disabled={busy} aria-label={`Resume ${source.name}`}>
              Resume
            </Button>
          ) : null}
          <RowMenu
            label={`More actions for ${source.name}`}
            disabled={busy}
            items={[
              { label: "Edit", onSelect: () => onAction("edit", source.id) },
              ...(paused ? [] : [{ label: "Fetch now", onSelect: () => onAction("fetch", source.id) }]),
              paused
                ? { label: "Resume", onSelect: () => onAction("resume", source.id) }
                : { label: "Pause", onSelect: () => onAction("pause", source.id) },
              { label: "Delete…", danger: true, onSelect: () => onAction("delete", source.id) },
            ]}
          />
        </span>
      );
    },
  }),
];

type Flash = { tone: "info" | "danger"; text: string };

function errorText(error: ApiError): string {
  return SOURCE_ERROR_TEXT[error.code] ?? displayMessage(error);
}

/** Keep a local copy of the list so mutations show at once; a server refresh replaces it. */
function useSourceList(fromServer: Source[]) {
  const [state, setState] = useState({ fromServer, list: fromServer });
  if (state.fromServer !== fromServer) setState({ fromServer, list: fromServer });
  const setList = (update: (list: Source[]) => Source[]) => setState((s) => ({ ...s, list: update(s.list) }));
  return [state.list, setList] as const;
}

export function SourcesScreen({ workspace, sources: serverSources, groups, canManage, initialSourceId: requested }: SourcesScreenProps) {
  const router = useRouter();
  const canEdit = canManage && !workspace.archived;
  const [sources, setSources] = useSourceList(serverSources);
  const [filter, setFilter] = useState<SourceFilter>(ALL_SOURCES);
  const [selectedId, setSelectedId] = useState<string | undefined>(() => initialSourceId(serverSources, requested));
  const [mode, setMode] = useState<DetailMode>("view");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [queued, setQueued] = useState<ReadonlySet<string>>(new Set());
  const [flash, setFlash] = useState<Flash | null>(null);
  const detailHeadingId = useId();

  const groupTags = new Map<string, GroupTag>(groups.map((g) => [g.id, { id: g.id, name: g.name, swatch: groupSwatch(g) }]));
  const failing = sources.filter((s) => s.status === "failing").length;
  const paused = sources.filter((s) => s.status === "paused").length;
  const hasWorkspaceWide = sources.some((s) => s.group_ids.length === 0);
  const visible = filterSources(sources, filter);
  const selected = sources.find((s) => s.id === selectedId);

  const focusDetail = () => requestAnimationFrame(() => document.getElementById(detailHeadingId)?.focus());

  function replace(next: Source) {
    setSources((list) => list.map((s) => (s.id === next.id ? next : s)));
  }

  async function runFetch(source: Source) {
    setBusyId(source.id);
    setFlash(null);
    const r = await settle(fetchSource({ client: browserApi, path: { workspace_id: workspace.id, source_id: source.id } }));
    setBusyId(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: `${source.name}: ${errorText(r.error)}` });
      return;
    }
    setQueued((q) => new Set(q).add(source.id));
    setFlash({ tone: "info", text: `${source.name}: ${FETCH_QUEUED_TEXT}` });
  }

  async function runStatus(source: Source, status: "active" | "paused") {
    setBusyId(source.id);
    setFlash(null);
    const r = await settle(
      updateSource({ client: browserApi, path: { workspace_id: workspace.id, source_id: source.id }, body: { status } }),
    );
    setBusyId(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: `${source.name}: ${errorText(r.error)}` });
      return;
    }
    replace(r.data);
    setFlash({
      tone: "info",
      text: status === "paused" ? `Paused ${source.name}.` : `Resumed ${source.name}. The next fetch runs within a minute.`,
    });
    router.refresh();
  }

  async function runDelete(source: Source) {
    setBusyId(source.id);
    setFlash(null);
    const r = await settle(deleteSource({ client: browserApi, path: { workspace_id: workspace.id, source_id: source.id } }));
    setBusyId(null);
    if (!r.ok) {
      setFlash({ tone: "danger", text: `${source.name}: ${errorText(r.error)}` });
      return;
    }
    const rest = sources.filter((s) => s.id !== source.id);
    setSources(() => rest);
    setSelectedId(initialSourceId(rest));
    setMode("view");
    setFlash({ tone: "info", text: `Deleted ${source.name}.` });
    router.refresh();
  }

  function onAction(action: RowAction, id: string) {
    const source = sources.find((s) => s.id === id);
    if (!source) return;
    switch (action) {
      case "select":
        setSelectedId(id);
        setMode("view");
        focusDetail();
        return;
      case "edit":
      case "delete":
        setSelectedId(id);
        setMode(action);
        focusDetail();
        return;
      case "fetch":
        void runFetch(source);
        return;
      case "pause":
        void runStatus(source, "paused");
        return;
      case "resume":
        void runStatus(source, "active");
    }
  }

  const rows: SourceRow[] = visible.map((s) => ({
    source: s,
    groups: s.group_ids.map((id) => groupTags.get(id)).filter((g): g is GroupTag => g !== undefined),
    selected: s.id === selectedId,
    canEdit,
    busy: busyId === s.id,
    onAction,
  }));

  const toggleView = (view: SourceFilter["view"]) => setFilter((f) => ({ ...f, view: f.view === view ? "all" : view }));
  const toggleGroup = (g: string) => setFilter((f) => ({ ...f, group: f.group === g ? undefined : g }));

  return (
    <>
      <Topbar
        title={SCREENS.sources.title}
        sub={sourcesSummary(sources)}
        action={canEdit ? { label: "Add source", href: `#${ADD_SOURCE_ID}` } : undefined}
      />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        {/* One live region for the outcome of row and detail actions. */}
        <div role="status" aria-live="polite" className="empty:hidden">
          {flash?.tone === "info" ? (
            <Notice
              tone="info"
              icon="check"
              actions={
                <Button size="sm" variant="ghost" onClick={() => setFlash(null)}>
                  Dismiss
                </Button>
              }
            >
              {flash.text}
            </Notice>
          ) : null}
        </div>
        {flash?.tone === "danger" ? (
          <Notice
            tone="danger"
            role="alert"
            actions={
              <Button size="sm" variant="ghost" onClick={() => setFlash(null)}>
                Dismiss
              </Button>
            }
          >
            {flash.text}
          </Notice>
        ) : null}

        <Panel>
          <div role="toolbar" aria-label="Filter sources" className="flex flex-wrap items-center gap-2 border-b border-border px-3.5 py-2.5">
            <Chip pressed={filter.view === "all" && !filter.group} onClick={() => setFilter(ALL_SOURCES)}>
              All
            </Chip>
            <Chip pressed={filter.view === "failing"} count={failing} onClick={() => toggleView("failing")}>
              Failing
            </Chip>
            <Chip pressed={filter.view === "paused"} count={paused} onClick={() => toggleView("paused")}>
              Paused
            </Chip>
            {groups.length > 0 ? (
              <>
                <span aria-hidden="true" className="h-5 w-px bg-border-strong" />
                {groups.map((g) => (
                  <Chip key={g.id} pressed={filter.group === g.id} onClick={() => toggleGroup(g.id)}>
                    <GroupDot group={g.id} style={{ background: groupSwatch(g) }} />
                    {g.name}
                  </Chip>
                ))}
                {hasWorkspaceWide ? (
                  <Chip pressed={filter.group === "workspace"} onClick={() => toggleGroup("workspace")}>
                    Workspace only
                  </Chip>
                ) : null}
              </>
            ) : null}
            <span className="flex-1" />
            <span className="text-caption text-fg-subtle max-narrow:hidden">A source with no group feeds the whole workspace.</span>
          </div>
          <PanelBody flush>
            {sources.length === 0 ? (
              <EmptyState
                icon="sources"
                tone="quiet"
                title="No sources yet"
                description={
                  canEdit
                    ? "Add an RSS or Atom feed below. Items start arriving after its first fetch, within a minute."
                    : "An editor adds the feeds this workspace reads. Nothing is ingested until then."
                }
              />
            ) : rows.length === 0 ? (
              <EmptyState
                icon="filter"
                tone="quiet"
                title="No sources match"
                description="Nothing matches these filters."
                action={
                  <Button size="sm" onClick={() => setFilter(ALL_SOURCES)}>
                    Show all
                  </Button>
                }
              />
            ) : (
              <DataTable
                columns={columns}
                data={rows}
                caption="Content sources"
                initialSorting={[{ id: "status", desc: true }]}
                getRowId={(r) => r.source.id}
              />
            )}
          </PanelBody>
          <PanelFoot>
            Items are scored separately for each group a source feeds — the same article can be a 0.9 for one group&apos;s
            pillars and a 0.2 for another&apos;s.
          </PanelFoot>
        </Panel>

        <div className="grid grid-cols-2 items-start gap-3.5 max-wide:grid-cols-1">
          {selected ? (
            <SourceDetail
              key={selected.id}
              source={selected}
              groups={groups}
              workspaceId={workspace.id}
              tz={workspace.timezone}
              canEdit={canEdit}
              mode={mode}
              onModeChange={setMode}
              busy={busyId === selected.id}
              queued={queued.has(selected.id)}
              onFetch={() => void runFetch(selected)}
              onStatus={(status) => void runStatus(selected, status)}
              onDelete={() => void runDelete(selected)}
              onSaved={(next) => {
                replace(next);
                setFlash({ tone: "info", text: `Saved ${next.name}.` });
                router.refresh();
              }}
              headingId={detailHeadingId}
            />
          ) : null}
          {canEdit ? (
            <AddSourcePanel
              workspaceId={workspace.id}
              groups={groups}
              onCreated={(source) => {
                setSources((list) => [...list.filter((s) => s.id !== source.id), source]);
                setSelectedId(source.id);
                setMode("view");
                setFilter(ALL_SOURCES);
                setFlash({ tone: "info", text: `Added ${source.name}. Its first fetch runs within a minute.` });
                router.refresh();
              }}
            />
          ) : null}
        </div>
      </main>
    </>
  );
}
