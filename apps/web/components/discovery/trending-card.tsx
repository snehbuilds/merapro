import Link from "next/link";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { Tag } from "@/components/ui/tag";
import { RelativeTime } from "@/components/ui/time";
import type { ContentItem, DuplicateRef } from "@/lib/api/models";
import { formatScore, relevanceTone, riskFlagLabel } from "@/lib/discovery";

/*
 * discovery.html .set cards: one ranked article, and the near-duplicates
 * collapsed into it. Every number is a score from the pipeline, printed as
 * is; nothing here is estimated by the client.
 */

/** Feed-supplied URLs are links only when they are http(s); never javascript: or data:. */
export function isWebUrl(url: string): boolean {
  try {
    const { protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

export function itemAnchor(id: string): string {
  return `item-${id}`;
}

function Meta({ children }: { children: ReactNode[] }) {
  const parts = children.filter(Boolean);
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-caption text-fg-subtle">
      {parts.map((part, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          {i > 0 ? <span aria-hidden="true">·</span> : null}
          {part}
        </span>
      ))}
    </div>
  );
}

export interface TrendingCardProps {
  item: ContentItem;
  tz: string;
  now: number;
  canEdit: boolean;
  busy: boolean;
  onDismiss: () => void;
  /**
   * "Draft for group": create a post set for the ranked group and open it,
   * link to the Composer to pick targets (whole-workspace view), or
   * disabled with a note saying why (viewers, archived workspaces).
   */
  draft: TrendingDraft;
}

export type TrendingDraft =
  | { kind: "create"; busy: boolean; disabled?: boolean; onDraft: () => void; noteId: string }
  | { kind: "link"; href: string; noteId: string }
  | { kind: "disabled"; noteId: string };

export function TrendingCard({ item, tz, now, canEdit, busy, onDismiss, draft }: TrendingCardProps) {
  const titleId = `${itemAnchor(item.id)}-title`;
  return (
    <article
      id={itemAnchor(item.id)}
      aria-labelledby={titleId}
      data-slot="trending-item"
      className="flex items-start gap-3.5 border-b border-border px-3.5 py-3.5 last:border-b-0 max-narrow:flex-col"
    >
      <div className="min-w-0 flex-1">
        <div className="mb-[3px] flex flex-wrap items-center gap-2">
          {item.relevance === null ? (
            <Tag tone="neutral">Not scored</Tag>
          ) : (
            <Tag tone={relevanceTone(item.relevance)}>{formatScore(item.relevance)} relevance</Tag>
          )}
          {item.best_pillar ? <Tag tone="outline">{item.best_pillar}</Tag> : null}
          {item.risk_flags.map((flag) => (
            <Tag key={flag} tone="warn">
              {riskFlagLabel(flag)}
            </Tag>
          ))}
          {item.coverage > 1 ? <span className="text-xs text-fg-subtle">Covered by {item.coverage} sources</span> : null}
        </div>
        <h3 id={titleId} className="text-md font-semibold">
          {isWebUrl(item.url) ? (
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
              {item.title}
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : (
            item.title
          )}
        </h3>
        {item.summary ? (
          <p className="mt-1 max-w-[78ch] text-sm text-fg-muted">{item.summary}</p>
        ) : (
          <p className="mt-1 max-w-[78ch] text-sm text-fg-subtle">
            Summary unavailable{item.state_reason === "summary_unavailable" ? ": the summariser couldn't read this article" : ""}. Open
            the article to judge it.
          </p>
        )}
        <Meta>
          {[
            item.source_name ?? "Unknown source",
            <RelativeTime key="age" value={item.published_at ?? item.fetched_at} tz={tz} now={now} />,
            item.freshness !== null ? `Freshness ${formatScore(item.freshness)}` : null,
            item.engagement_potential !== null ? (
              <span key="eng" title={item.engagement_rationale ?? undefined}>
                Engagement potential {formatScore(item.engagement_potential)}
              </span>
            ) : null,
          ]}
        </Meta>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {canEdit ? (
          <Button size="sm" onClick={onDismiss} disabled={busy} aria-label={`Dismiss ${item.title}`}>
            {busy ? "Dismissing…" : "Dismiss"}
          </Button>
        ) : null}
        {draft.kind === "create" ? (
          <Button
            size="sm"
            variant="primary"
            onClick={draft.onDraft}
            disabled={draft.busy || draft.disabled}
            aria-describedby={draft.noteId}
          >
            {draft.busy ? "Creating…" : "Draft for group"}
          </Button>
        ) : draft.kind === "link" ? (
          <Button asChild size="sm" variant="primary">
            <Link href={draft.href} aria-describedby={draft.noteId}>
              Draft for group
            </Link>
          </Button>
        ) : (
          <Button size="sm" variant="primary" disabled aria-describedby={draft.noteId}>
            Draft for group
          </Button>
        )}
      </div>
    </article>
  );
}

export function DuplicateCard({ duplicate, original, tz, now }: { duplicate: DuplicateRef; original: ContentItem; tz: string; now: number }) {
  const titleId = `dup-${duplicate.id}-title`;
  return (
    <article
      aria-labelledby={titleId}
      data-slot="duplicate-item"
      className="flex items-start gap-3.5 border-b border-border bg-surface-muted px-3.5 py-3 last:border-b-0 max-narrow:flex-col"
    >
      <div className="min-w-0 flex-1">
        <div className="mb-[3px] flex flex-wrap items-center gap-2">
          <Tag tone="outline">Duplicate</Tag>
        </div>
        <h3 id={titleId} className="text-sm font-semibold text-fg-muted">
          {duplicate.title}
        </h3>
        <p className="mt-1 max-w-[78ch] text-sm text-fg-muted">
          Collapsed into{" "}
          <a href={`#${itemAnchor(original.id)}`} className="font-medium text-accent hover:underline">
            {original.title}
          </a>
          {duplicate.similarity !== null ? ` — semantic similarity ${formatScore(duplicate.similarity)}` : ""}. Kept rather
          than discarded, because coverage across sources is itself a relevance signal.
        </p>
        <Meta>{[duplicate.source_name ?? "Unknown source", <RelativeTime key="age" value={duplicate.fetched_at} tz={tz} now={now} />]}</Meta>
      </div>
      <StatusPill tone="neutral" shape="ring" label="Deduplicated" />
    </article>
  );
}

export function DismissedRow({ item, busy, onUndo }: { item: ContentItem; busy: boolean; onUndo: () => void }) {
  return (
    <div
      data-slot="dismissed-item"
      className="flex items-center gap-3 border-b border-border px-3.5 py-2.5 text-sm text-fg-muted last:border-b-0"
    >
      <span className="min-w-0 flex-1 truncate">
        Dismissed <span className="text-fg">{item.title}</span>. It no longer shows in Discovery for anyone.
      </span>
      <Button size="sm" variant="ghost" onClick={onUndo} disabled={busy} aria-label={`Undo dismiss of ${item.title}`}>
        {busy ? "Restoring…" : "Undo"}
      </Button>
    </div>
  );
}
