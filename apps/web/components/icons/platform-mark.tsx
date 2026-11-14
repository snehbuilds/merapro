import type { SVGProps } from "react";
import { cn } from "@/lib/utils";
import { PLATFORM_PATHS, type Platform } from "./paths";

export const PLATFORM_LABELS: Record<Platform, string> = {
  linkedin: "LinkedIn",
  x: "X",
  facebook: "Facebook",
  instagram: "Instagram",
  threads: "Threads",
  bluesky: "Bluesky",
  mastodon: "Mastodon",
};

const SIZE = { sm: "size-3.5", md: "size-4", lg: "size-5" } as const;

export interface PlatformMarkProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  platform: Platform;
  size?: keyof typeof SIZE;
  /** Platform marks are filled glyphs; announce them unless a visible label sits next to them. */
  decorative?: boolean;
}

export function PlatformMark({
  platform,
  size = "md",
  decorative = false,
  className,
  ...props
}: PlatformMarkProps) {
  const a11y = decorative
    ? ({ "aria-hidden": true, focusable: false } as const)
    : ({ role: "img", "aria-label": PLATFORM_LABELS[platform] } as const);
  return (
    <svg
      viewBox="0 0 16 16"
      xmlns="http://www.w3.org/2000/svg"
      data-slot="platform-mark"
      className={cn("block shrink-0 fill-current stroke-none", SIZE[size], className)}
      {...a11y}
      {...props}
    >
      {PLATFORM_PATHS[platform]}
    </svg>
  );
}
