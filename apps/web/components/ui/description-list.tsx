import { Fragment, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .dl: a two-column ledger, term left in muted ink, value right,
 * dotted rules between rows and none under the last.
 */
export interface DescriptionItem {
  term: ReactNode;
  detail: ReactNode;
  /** Machine data (ids, scopes, hashes) renders in mono. */
  mono?: boolean;
}

export interface DescriptionListProps extends Omit<ComponentProps<"dl">, "children"> {
  items: readonly DescriptionItem[];
}

export function DescriptionList({ items, className, ...props }: DescriptionListProps) {
  return (
    <dl
      data-slot="description-list"
      className={cn("grid grid-cols-[auto_1fr] items-baseline gap-x-3.5 text-sm", className)}
      {...props}
    >
      {items.map((item, i) => {
        const rule = i < items.length - 1 ? "border-b border-dotted border-border-strong" : "";
        return (
          <Fragment key={i}>
            <dt className={cn("py-1.5 text-fg-muted", rule)}>{item.term}</dt>
            <dd className={cn("py-1.5 text-right [overflow-wrap:anywhere]", item.mono && "font-mono text-xs", rule)}>
              {item.detail}
            </dd>
          </Fragment>
        );
      })}
    </dl>
  );
}
