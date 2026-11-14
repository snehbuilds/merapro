import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/* mera.css kbd: mono, raised, 2px bottom border like a physical key. */
export function Kbd({ className, ...props }: ComponentProps<"kbd">) {
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "inline-block min-w-[18px] rounded-sm border border-b-2 border-border-strong bg-surface-raised px-1 py-[3px]",
        "text-center font-mono text-2xs leading-none font-medium text-fg-muted",
        className,
      )}
      {...props}
    />
  );
}
