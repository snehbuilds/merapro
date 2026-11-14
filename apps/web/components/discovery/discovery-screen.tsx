"use client";

import { contentStats, createPostSet, dismissContentItem, listPillars, trending, type GroupOut } from "@mera/sdk";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";
import { GroupDot } from "@/components/mera/group-dot";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { DescriptionList } from "@/components/ui/description-list";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { displayMessage, type ApiError } from "@/lib/api/errors";
import type { ContentItem, ContentStats, Pillar } from "@/lib/api/models";
import { POST_SET_ERROR_TEXT, composerHref, rememberSkipped } from "@/lib/composer";
import {
  DISCOVERY_ERROR_TEXT,
  MIN_RELEVANCE,
  TRENDING_LIMIT,
  WINDOWS,
  WINDOW_HOURS,
  WINDOW_LABEL,
  discoveryQuery,
  formatScore,
  type DiscoveryState,
} from "@/lib/discovery";
import { groupSwatch } from "@/lib/groups";
import { SCREENS, screenHref } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { PillarsPanel } from "./pillars-panel";
import { DismissedRow, DuplicateCard, TrendingCard } from "./trending-card";

/*
 * Content Discovery (discovery.html): what came in, ranked for one group's
 * pillars (or the best score across groups for the whole workspace), with
 * the pipeline's scores printed as they are. Filters live in the URL
 * (?group=&window=&min=) so a reload keeps them. Editors and up dismiss
 * items, edit pillars and draft: "Draft for group" creates a post set for
 * the ranked group and opens it in the Composer (M5); in the whole-workspace
 * view it opens the Composer with the item, to pick targets there.
 */

export interface DiscoveryScreenProps {
  workspace: { id: string; slug: string; name: string; timezone: string; archived: boolean };
  groups: GroupOut[];
  initialState: DiscoveryState;
  items: ContentItem[];
  /** null when the stats couldn't be loaded. */
  stats: ContentStats | null;
  /** null when the pillars couldn't be loaded. */
  pillars: Pillar[] | null;
  /** Editor+ (EDIT_POSTS for dismiss and drafting, CONFIGURE_CONTENT for pillars). */
  canManage: boolean;
}

interface Data {
  items: ContentItem[];
  stats: ContentStats | null;
  pillars: Pillar[] | null;
}

type Announcement = { tone: "info" | "danger"; text: string };

function errorText(error: ApiError): string {
  return DISCOVERY_ERROR_TEXT[error.code] ?? displayMessage(error);
}

/** The clock for relative ages ("2h ago"), read when data arrives, not on every render. */
function readClock(): number {
  return Date.now();
}

function NotYet() {
  return (
    <span className="text-fg-subtle">
      <span aria-hidden="true">—</span>
      <span className="sr-only">Not available yet</span>
    </span>
  );
}

export function DiscoveryScreen({ workspace, groups, initialState, items, stats, pillars, canManage }: DiscoveryScreenProps) {
  const router = useRouter();
  const canEdit = canManage && !workspace.archived;
  const [draftingId, setDraftingId] = useState<string | null>(null);
  const [state, setState] = useState<DiscoveryState>(initialState);
  const [data, setData] = useState<Data>({ items, stats, pillars });
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [now, setNow] = useState(readClock);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());
  const [busyId, setBusyId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState<Announcement | null>(null);
  const request = useRef(0);
  const draftNoteId = useId();

  const group = state.groupId ? groups.find((g) => g.id === state.groupId) : undefined;
  const rankedFor = group ? group.name : "the whole workspace";

  /** Load what a state needs; drop responses that a newer request has overtaken. */
  async function load(next: DiscoveryState, opts: { withMeta: boolean }) {
    const id = ++request.current;
    setLoading(true);
    setLoadError(null);
    const path = { workspace_id: workspace.id };
    const [t, s, p] = await Promise.all([
      settle(
        trending({
          client: browserApi,
          path,
          query: {
            group_id: next.groupId,
            hours: WINDOW_HOURS[next.window],
            min_relevance: next.min ? MIN_RELEVANCE : 0,
            limit: TRENDING_LIMIT,
          },
        }),
      ),
      opts.withMeta ? settle(contentStats({ client: browserApi, path, query: { group_id: next.groupId, days: 7 } })) : null,
      opts.withMeta ? settle(listPillars({ client: browserApi, path, query: { group_id: next.groupId } })) : null,
    ]);
    if (id !== request.current) return;
    setLoading(false);
    setNow(readClock());
    if (!t.ok) {
      setLoadError(errorText(t.error));
      return;
    }
    setDismissed(new Set());
    setData((d) => ({
      items: t.data.data,
      stats: s ? (s.ok ? s.data : null) : d.stats,
      pillars: p ? (p.ok ? p.data.data : null) : d.pillars,
    }));
  }

  function choose(patch: Partial<DiscoveryState>) {
    const next = { ...state, ...patch };
    if (next.groupId === state.groupId && next.window === state.window && next.min === state.min) return;
    setState(next);
    setAnnouncement(null);
    // Reflect the choice in the URL so a reload (or a shared link) keeps it.
    window.history.replaceState(null, "", `${window.location.pathname}${discoveryQuery(next)}`);
    void load(next, { withMeta: next.groupId !== state.groupId });
  }

  async function dismiss(item: ContentItem, undo: boolean) {
    setBusyId(item.id);
    setAnnouncement(null);
    const r = await settle(
      dismissContentItem({
        client: browserApi,
        path: { workspace_id: workspace.id, item_id: item.id },
        body: undo ? { undo: true, group_id: state.groupId } : { group_id: state.groupId },
      }),
    );
    setBusyId(null);
    if (!r.ok) {
      setAnnouncement({ tone: "danger", text: errorText(r.error) });
      return;
    }
    setDismissed((d) => {
      const nextSet = new Set(d);
      if (undo) nextSet.delete(item.id);
      else nextSet.add(item.id);
      return nextSet;
    });
    setAnnouncement({ tone: "info", text: undo ? `Restored “${item.title}”.` : `Dismissed “${item.title}”. Undo is next to it.` });
  }

  /** Create a post set for the ranked group and open it in the Composer. */
  async function draftForGroup(item: ContentItem, groupId: string) {
    setDraftingId(item.id);
    setAnnouncement(null);
    const r = await settle(
      createPostSet({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspace.id },
        body: { content_item_id: item.id, targets: { account_group_ids: [groupId] }, generate: true },
      }),
    );
    if (!r.ok) {
      setDraftingId(null);
      setAnnouncement({ tone: "danger", text: POST_SET_ERROR_TEXT[r.error.code] ?? displayMessage(r.error) });
      return;
    }
    rememberSkipped(r.data.id, r.data.skipped ?? []);
    router.push(composerHref(workspace.slug, { set: r.data.id }));
  }

  function draftFor(item: ContentItem) {
    if (!canEdit) return { kind: "disabled" as const, noteId: draftNoteId };
    if (!state.groupId) return { kind: "link" as const, href: composerHref(workspace.slug, { item: item.id }), noteId: draftNoteId };
    const groupId = state.groupId;
    return {
      kind: "create" as const,
      busy: draftingId === item.id,
      disabled: draftingId !== null,
      onDraft: () => void draftForGroup(item, groupId),
      noteId: draftNoteId,
    };
  }

  const visible = data.items;
  const empty = visible.length === 0;

  function emptyState() {
    if (data.stats && data.stats.ingested === 0) {
      return (
        <EmptyState
          icon="sources"
          title="Nothing ingested yet"
          description="Discovery ranks what your sources bring in. Add an RSS feed on Sources; items show here after its first fetch."
          action={
            <Button asChild size="sm">
              <Link href={screenHref(workspace.slug, "sources")}>Go to Sources</Link>
            </Button>
          }
        />
      );
    }
    if (state.groupId && data.pillars && data.pillars.length === 0) {
      return (
        <EmptyState
          icon="discovery"
          title={`No pillars for ${rankedFor}`}
          description="Relevance is similarity to a group's pillars. Add a pillar below and items are scored against it straight away."
        />
      );
    }
    return (
      <EmptyState
        icon="discovery"
        title="Nothing trending here"
        description={`No items from the ${state.window === "24h" ? "last 24 hours" : "last 7 days"}${state.min ? ` scored above ${formatScore(MIN_RELEVANCE)}` : ""} for ${rankedFor}.`}
        action={
          state.min ? (
            <Button size="sm" onClick={() => choose({ min: false })}>
              Show every score
            </Button>
          ) : state.window === "24h" ? (
            <Button size="sm" onClick={() => choose({ window: "7d" })}>
              Widen to 7 days
            </Button>
          ) : undefined
        }
      />
    );
  }

  return (
    <>
      <Topbar title={SCREENS.discovery.title} sub={group ? `Trending for ${group.name}` : "Trending across the workspace"} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div role="status" aria-live="polite" className="empty:hidden">
          {announcement?.tone === "info" ? (
            <Notice tone="info" icon="check">
              {announcement.text}
            </Notice>
          ) : null}
        </div>
        {announcement?.tone === "danger" ? (
          <Notice tone="danger" role="alert">
            {announcement.text}
          </Notice>
        ) : null}

        <Panel>
          <div role="toolbar" aria-label="Rank and filter" className="flex flex-wrap items-center gap-2 border-b border-border px-3.5 py-2.5">
            <span className="text-sm font-semibold whitespace-nowrap" id={`${draftNoteId}-ranked`}>
              Ranked for
            </span>
            <span role="group" aria-labelledby={`${draftNoteId}-ranked`} className="flex flex-wrap items-center gap-2">
              {groups.map((g) => (
                <Chip key={g.id} pressed={state.groupId === g.id} onClick={() => choose({ groupId: g.id })}>
                  <GroupDot group={g.id} style={{ background: groupSwatch(g) }} />
                  {g.name}
                </Chip>
              ))}
              <Chip pressed={state.groupId === null} onClick={() => choose({ groupId: null })}>
                Whole workspace
              </Chip>
            </span>
            <span aria-hidden="true" className="h-5 w-px bg-border-strong" />
            {WINDOWS.map((w) => (
              <Chip key={w} pressed={state.window === w} onClick={() => choose({ window: w })}>
                {WINDOW_LABEL[w]}
              </Chip>
            ))}
            <Chip pressed={state.min} onClick={() => choose({ min: !state.min })}>
              Relevance &gt; {MIN_RELEVANCE}
            </Chip>
            <span className="flex-1" />
            <span className="text-caption text-fg-subtle max-narrow:hidden">
              {group ? "Scores are against this group's pillars, not the workspace's." : "Each item shows its best score across groups."}
            </span>
          </div>

          <div aria-busy={loading} aria-labelledby={`${draftNoteId}-list`} role="region" className={cn(loading && "opacity-60")}>
            <h2 id={`${draftNoteId}-list`} className="sr-only">
              Ranked items for {rankedFor}
            </h2>
            {loadError ? (
              <Notice tone="danger" role="alert" className="m-3.5">
                Couldn&apos;t load trending items. {loadError}
              </Notice>
            ) : empty ? (
              emptyState()
            ) : (
              visible.map((item) =>
                dismissed.has(item.id) ? (
                  <DismissedRow key={item.id} item={item} busy={busyId === item.id} onUndo={() => void dismiss(item, true)} />
                ) : (
                  <div key={item.id}>
                    <TrendingCard
                      item={item}
                      tz={workspace.timezone}
                      now={now}
                      canEdit={canEdit}
                      busy={busyId === item.id}
                      onDismiss={() => void dismiss(item, false)}
                      draft={draftFor(item)}
                    />
                    {(item.duplicates ?? []).map((d) => (
                      <DuplicateCard key={d.id} duplicate={d} original={item} tz={workspace.timezone} now={now} />
                    ))}
                  </div>
                ),
              )
            )}
          </div>

          <PanelFoot className="grid gap-1">
            <p>
              Relevance = similarity to this group&apos;s pillar embeddings, weighted by source weight and decayed by age.
              Arithmetic, not a model judgement, so the same input always produces the same score.
            </p>
            <p id={draftNoteId}>
              {!canEdit
                ? workspace.archived
                  ? "Drafting is off while this workspace is archived."
                  : "Editors and up draft posts from these items."
                : group
                  ? `Draft for group creates a post for each account in ${group.name} and opens it in the Composer.`
                  : "Draft for group opens the Composer with the item, so you pick the groups and accounts there."}
            </p>
          </PanelFoot>
        </Panel>

        <div className="grid grid-cols-3 items-start gap-3.5 max-wide:grid-cols-1">
          <PillarsPanel
            key={state.groupId ?? "workspace"}
            workspaceId={workspace.id}
            groups={groups}
            groupId={state.groupId}
            pillars={data.pillars}
            canEdit={canEdit}
            onChanged={(message) => {
              setAnnouncement({ tone: "info", text: message });
              void load(state, { withMeta: true });
            }}
          />

          <Panel aria-label="Last 7 days">
            <PanelHead title="Last 7 days" />
            <PanelBody className="grid gap-2.5">
              {data.stats ? (
                <>
                  <DescriptionList
                    items={[
                      { term: "Items ingested", detail: data.stats.ingested },
                      { term: "After dedupe", detail: data.stats.after_dedupe },
                      { term: `Above threshold (${formatScore(data.stats.threshold)})`, detail: data.stats.above_threshold },
                      { term: "Filtered by keywords", detail: data.stats.filtered },
                      { term: "Drafted", detail: data.stats.drafted ?? <NotYet /> },
                      { term: "Published", detail: data.stats.published ?? <NotYet /> },
                    ]}
                  />
                  <p className="text-caption text-fg-subtle">
                    Drafted and published fill in once drafting (M5) and publishing (M8) arrive.
                  </p>
                </>
              ) : (
                <p className="text-sm text-fg-muted">Couldn&apos;t load the counts. Reload to try again.</p>
              )}
            </PanelBody>
          </Panel>

          <Panel aria-label="Best sources">
            <PanelHead title="Best sources" />
            <EmptyState
              icon="analytics"
              title="No published posts yet"
              description="Best sources rank by how often a source's items end up published. That needs published posts, so this fills in once publishing is live."
            />
          </Panel>
        </div>
      </main>
    </>
  );
}
