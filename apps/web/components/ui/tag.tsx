import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/* mera.css .tag and tones. Outline tags carry scores and counts: machine data, so mono. */
const tagVariants = cva(
  "inline-flex h-5.5 items-center gap-1.5 whitespace-nowrap rounded-sm px-2 text-xs font-medium",
  {
    variants: {
      tone: {
        neutral: "bg-neutral-bg text-neutral",
        ok: "bg-ok-bg text-ok",
        warn: "bg-warn-bg text-warn",
        danger: "bg-danger-bg text-danger",
        info: "bg-info-bg text-info",
        outline: "tabular border border-border-strong bg-transparent font-mono text-mono-xs text-fg-muted",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface TagProps extends ComponentProps<"span">, VariantProps<typeof tagVariants> {}

export function Tag({ tone, className, ...props }: TagProps) {
  return <span data-slot="tag" className={cn(tagVariants({ tone }), className)} {...props} />;
}
