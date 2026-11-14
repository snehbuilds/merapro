import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .bar: a 6px ledger rule that fills.
 *   ProgressBar  progress toward done (decisions 1 / 4): role="progressbar".
 *   Meter        a level within a range (quota 62%): role="meter"; tone can be
 *                given or derived from warn / danger thresholds.
 * Tone is never the only cue: callers show the number next to the bar.
 */
export type ProgressTone = "accent" | "ok" | "warn" | "danger";

const FILL: Record<ProgressTone, string> = {
  accent: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
};

interface BarProps extends Omit<ComponentProps<"div">, "role" | "children"> {
  value: number;
  max?: number;
  /** Accessible name. Required unless aria-labelledby is given. */
  label?: string;
  /** Spoken value, e.g. "1 of 4 decided". Defaults to the percentage. */
  valueText?: string;
}

function Bar({
  role,
  value,
  max = 100,
  label,
  valueText,
  tone,
  className,
  ...props
}: BarProps & { role: "progressbar" | "meter"; tone: ProgressTone }) {
  const clamped = Math.min(Math.max(value, 0), max);
  const pct = max > 0 ? (clamped / max) * 100 : 0;
  return (
    <div
      role={role}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={clamped}
      aria-valuetext={valueText}
      data-slot={role === "meter" ? "meter" : "progress"}
      data-tone={tone}
      className={cn("h-1.5 overflow-hidden rounded-[1px] bg-surface-active", className)}
      {...props}
    >
      <i className={cn("block h-full", FILL[tone])} style={{ width: `${pct}%` }} />
    </div>
  );
}

export interface ProgressBarProps extends BarProps {
  tone?: ProgressTone;
}

export function ProgressBar({ tone = "accent", ...props }: ProgressBarProps) {
  return <Bar role="progressbar" tone={tone} {...props} />;
}

export interface MeterProps extends BarProps {
  tone?: ProgressTone;
  /** Fractions of max. Used only when `tone` is not given. */
  thresholds?: { warn?: number; danger?: number };
}

export function meterTone(value: number, max: number, thresholds: MeterProps["thresholds"] = {}): ProgressTone {
  const f = max > 0 ? value / max : 0;
  if (thresholds.danger !== undefined && f >= thresholds.danger) return "danger";
  if (thresholds.warn !== undefined && f >= thresholds.warn) return "warn";
  return "accent";
}

export function Meter({ tone, thresholds = { warn: 0.6, danger: 0.9 }, max = 100, ...props }: MeterProps) {
  return <Bar role="meter" tone={tone ?? meterTone(props.value, max, thresholds)} max={max} {...props} />;
}
