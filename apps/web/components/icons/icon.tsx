import type { SVGProps } from "react";
import { cn } from "@/lib/utils";
import { UI_ICON_PATHS, type IconName } from "./paths";

/** Sizes from mera.css: .icon--sm 14, .icon 16, .icon--lg 20, .icon--xl 26 (stroke 1.25). */
const SIZE = {
  sm: "size-3.5",
  md: "size-4",
  lg: "size-5",
  xl: "size-[26px] [stroke-width:1.25]",
} as const;

export type IconSize = keyof typeof SIZE;

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  name: IconName;
  size?: IconSize;
  /** When set, the icon is announced (role="img"). Otherwise it is decorative. */
  label?: string;
}

export function Icon({ name, size = "md", label, className, ...props }: IconProps) {
  const a11y = label
    ? ({ role: "img", "aria-label": label } as const)
    : ({ "aria-hidden": true, focusable: false } as const);
  return (
    <svg
      viewBox="0 0 16 16"
      xmlns="http://www.w3.org/2000/svg"
      data-slot="icon"
      className={cn(
        "block shrink-0 fill-none stroke-current [stroke-linecap:round] [stroke-linejoin:round] [stroke-width:1.5]",
        SIZE[size],
        className,
      )}
      {...a11y}
      {...props}
    >
      {UI_ICON_PATHS[name]}
    </svg>
  );
}
