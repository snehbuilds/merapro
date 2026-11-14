"use client";

import { useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .inbox / .split--left: a list of selectable rows beside the detail
 * of the selected one. Rows are buttons; the selected row carries
 * aria-current="true" and the accent-soft fill (mera.css .msg[aria-current]).
 * At ≤900px the panes stack, list first.
 */
export interface TwoPaneListProps<T> {
  items: readonly T[];
  getKey: (item: T) => string;
  renderItem: (item: T, selected: boolean) => ReactNode;
  renderDetail: (item: T) => ReactNode;
  /** Accessible name for the list. */
  label: string;
  selectedKey?: string;
  defaultSelectedKey?: string;
  onSelect?: (key: string) => void;
  empty?: ReactNode;
  className?: string;
}

export function TwoPaneList<T>({
  items,
  getKey,
  renderItem,
  renderDetail,
  label,
  selectedKey,
  defaultSelectedKey,
  onSelect,
  empty,
  className,
}: TwoPaneListProps<T>) {
  const first = items[0];
  const [internal, setInternal] = useState<string | undefined>(
    defaultSelectedKey ?? (first ? getKey(first) : undefined),
  );
  const current = selectedKey ?? internal;
  const selected = items.find((i) => getKey(i) === current);

  return (
    <div
      data-slot="two-pane-list"
      className={cn(
        "grid grid-cols-[350px_minmax(0,1fr)] max-wide:grid-cols-[290px_minmax(0,1fr)] max-rail:grid-cols-[minmax(0,1fr)]",
        className,
      )}
    >
      <ul
        aria-label={label}
        className="min-w-0 border-r border-border max-rail:border-r-0 max-rail:border-b"
      >
        {items.map((item) => {
          const key = getKey(item);
          const isSel = key === current;
          return (
            <li key={key}>
              <button
                type="button"
                aria-current={isSel ? "true" : undefined}
                onClick={() => {
                  if (selectedKey === undefined) setInternal(key);
                  onSelect?.(key);
                }}
                className={cn(
                  "grid w-full gap-[5px] border-b border-border px-3.5 py-3 text-left hover:bg-surface-hover",
                  "aria-[current=true]:bg-accent-soft",
                )}
              >
                {renderItem(item, isSel)}
              </button>
            </li>
          );
        })}
      </ul>
      <section aria-label={`${label}: selected`} className="min-w-0">
        {selected ? renderDetail(selected) : empty}
      </section>
    </div>
  );
}
