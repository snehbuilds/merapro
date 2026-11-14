import type { ComponentProps, ReactNode } from "react";
import { countGraphemes } from "@/lib/graphemes";
import { cn } from "@/lib/utils";

/*
 * mera.css .counter: "344 / 3000" in mono. Counts grapheme clusters
 * (lib/graphemes.ts), so an emoji with a skin tone or a letter with a combining
 * accent is one character, as the platforms count it. The limit comes from the
 * account's capability descriptor (max_text_length), never a platform check.
 *
 * Over the limit the counter turns danger ink and bold, and adds sr text with
 * the overage, so the state is not carried by colour alone.
 */
export interface CharCounterProps extends Omit<ComponentProps<"span">, "children"> {
  /** The text to count. Ignored when `count` is given. */
  text?: string;
  /** A precomputed count (e.g. weighted by the platform). */
  count?: number;
  limit: number;
  /** Trailing context, e.g. "part 1 of 3". */
  suffix?: ReactNode;
}

export function CharCounter({ text = "", count, limit, suffix, className, ...props }: CharCounterProps) {
  const n = count ?? countGraphemes(text);
  const over = n > limit;
  return (
    <span
      data-slot="char-counter"
      data-over={over || undefined}
      className={cn("tabular font-mono text-mono-xs", over && "font-semibold text-danger", className)}
      {...props}
    >
      {n} / {limit}
      {over ? <span className="sr-only">, {n - limit} over the limit</span> : null}
      {suffix ? <> · {suffix}</> : null}
    </span>
  );
}
