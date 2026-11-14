import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .btngroup: buttons share borders; only the outer corners are round.
 * role="group" + an aria-label names the set for assistive tech.
 */
export function ButtonGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      role="group"
      data-slot="button-group"
      className={cn(
        "inline-flex",
        "[&>[data-slot=button]]:-ml-px [&>[data-slot=button]]:rounded-none [&>[data-slot=button]]:shadow-none",
        "[&>[data-slot=button]:first-child]:ml-0 [&>[data-slot=button]:first-child]:rounded-l-md",
        "[&>[data-slot=button]:last-child]:rounded-r-md",
        // Keep the focused or hovered button's border on top of its neighbours.
        "[&>[data-slot=button]:focus-visible]:z-10 [&>[data-slot=button]:hover]:z-10",
        className,
      )}
      {...props}
    />
  );
}
