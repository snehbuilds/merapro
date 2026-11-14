import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/*
 * tailwind-merge must know the custom type scale from globals.css, otherwise it
 * reads `text-caption` as a colour and drops it when merged with `text-ok`.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["2xs", "mono-xs", "caption", "body", "md"] }],
      shadow: [{ shadow: ["popover"] }],
      "font-family": [{ font: ["ui", "label"] }],
    },
  },
});

/** shadcn/ui convention: merge conditional classes, last Tailwind utility wins. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
