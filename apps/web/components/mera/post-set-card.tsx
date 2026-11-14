"use client";

import { useId, useState, type ComponentProps, type ElementType, type ReactNode } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { expiryStatus } from "@/lib/expiry";
import { formatInZone, zoneAbbreviation } from "@/lib/time";
import { cn } from "@/lib/utils";
import { GroupLabel } from "./group-dot";
import { VariantList } from "./variant-row";

/*
 * mera.css .set: one story, one post per account. The head carries the story
 * title and a meta line (group · account count · source · stagger window ·
 * expiry urgency); the body is the set's VariantRows on a drawn branch.
 *
 * The chevron is a disclosure button (aria-expanded + aria-controls). Its name
 * stays fixed ("Accounts in …"); the expanded state is announced by
 * aria-expanded, not by swapping the label as mera.js does.
 */
export interface PostSetCardProps extends Omit<ComponentProps<"article">, "title"> {
  title: ReactNode;
  /** Plain-text title for the disclosure's accessible name. */
  titleText?: string;
  headingLevel?: Extract<ElementType, "h2" | "h3" | "h4">;
  group: { key: number | string; name: ReactNode };
  accountCount: number;
  /** Source domain / feed, or "Manual". */
  source?: ReactNode;
  stagger?: { start: Date | string; end: Date | string; tz: string };
  /** Expiry of the approval request. `now` is passed in so render stays pure. */
  expiry?: { at: Date | string; now: Date | string };
  /** Right side of the head: tags and actions. */
  aside?: ReactNode;
  expanded?: boolean;
  defaultExpanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
  /** VariantRows. */
  children?: ReactNode;
}

function Sep() {
  return (
    <span aria-hidden="true" className="text-border-strong">
      ·
    </span>
  );
}

export function PostSetCard({
  title,
  titleText,
  headingLevel: Heading = "h3",
  group,
  accountCount,
  source,
  stagger,
  expiry,
  aside,
  expanded,
  defaultExpanded = true,
  onExpandedChange,
  children,
  className,
  ...props
}: PostSetCardProps) {
  const [internal, setInternal] = useState(defaultExpanded);
  const open = expanded ?? internal;
  const bodyId = useId();
  const exp = expiry ? expiryStatus(expiry.at, expiry.now) : null;
  const name = typeof title === "string" ? title : (titleText ?? "this set");

  const toggle = () => {
    if (expanded === undefined) setInternal(!open);
    onExpandedChange?.(!open);
  };

  return (
    <article
      data-slot="post-set-card"
      data-expanded={open}
      className={cn("border-b border-border last:border-b-0", className)}
      {...props}
    >
      <div className="flex items-start gap-3 p-3.5 max-rail:flex-wrap">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-expanded={open}
          aria-controls={bodyId}
          aria-label={`Accounts in ${name}`}
          onClick={toggle}
        >
          <Icon name={open ? "chevdown" : "chevright"} size="sm" />
        </Button>
        <div className="min-w-0 flex-1">
          <Heading className="text-[14.5px] font-semibold tracking-[-0.006em]">{title}</Heading>
          <div className="mt-[5px] flex flex-wrap items-center gap-x-2.5 gap-y-[5px] text-caption text-fg-muted">
            <GroupLabel group={group.key} name={group.name} />
            <Sep />
            <span>
              {accountCount} {accountCount === 1 ? "account" : "accounts"}
            </span>
            {source ? (
              <>
                <Sep />
                <span>{source}</span>
              </>
            ) : null}
            {stagger ? (
              <>
                <Sep />
                <span className="inline-flex items-center gap-1">
                  <Icon name="stagger" size="sm" />
                  <span className="sr-only">Staggered </span>
                  <span className="tabular font-mono text-xs">
                    {formatInZone(stagger.start, stagger.tz)}–{formatInZone(stagger.end, stagger.tz)}{" "}
                    <span className="text-fg-subtle" suppressHydrationWarning>
                      {zoneAbbreviation(stagger.start, stagger.tz)}
                    </span>
                  </span>
                </span>
              </>
            ) : null}
            {exp ? (
              <>
                <Sep />
                <StatusPill tone={exp.tone} shape={exp.shape} label={exp.label} data-urgency={exp.urgency} />
              </>
            ) : null}
          </div>
        </div>
        {aside ? (
          <div className="flex shrink-0 items-center gap-2.5 max-rail:flex-wrap max-narrow:w-full">{aside}</div>
        ) : null}
      </div>
      <VariantList id={bodyId} hidden={!open}>
        {children}
      </VariantList>
    </article>
  );
}
