import type { ComponentProps } from "react";
import { formatInZone, relativeAge, toDate, zoneAbbreviation, type TimeFormat } from "@/lib/time";
import { cn } from "@/lib/utils";

/*
 * Every timestamp renders in the workspace timezone with the zone shown
 * (docs/12 §3 F2): "09:00 PDT". Mono, tabular, in a <time> element whose
 * dateTime is the exact ISO instant. The full zone name is in the title.
 * Zone abbreviations are normalised in lib/time.ts so server and client agree.
 */
export interface TimeProps extends Omit<ComponentProps<"time">, "dateTime" | "children"> {
  value: Date | string | number;
  /** IANA timezone, normally the workspace's (e.g. "America/Los_Angeles"). */
  tz: string;
  format?: TimeFormat;
  /** Hide the zone only where a column header already states it. */
  showZone?: boolean;
}

export function Time({ value, tz, format = "time", showZone = true, className, ...props }: TimeProps) {
  const date = toDate(value);
  const text = formatInZone(date, tz, format);
  const zone = zoneAbbreviation(date, tz);
  return (
    <time
      data-slot="time"
      dateTime={date.toISOString()}
      title={`${formatInZone(date, tz, "datetime")} ${zone} (${tz})`}
      className={cn("tabular font-mono text-xs whitespace-nowrap", className)}
      // ICU data can differ between the server and the browser; the instant is
      // the same, so a differing rendering is not worth a re-render.
      suppressHydrationWarning
      {...props}
    >
      {text}
      {/* A real space, not a margin: assistive tech reads text content, not layout. */}
      {showZone ? (
        <>
          {" "}
          <span className="text-fg-subtle" suppressHydrationWarning>
            {zone}
          </span>
        </>
      ) : null}
    </time>
  );
}

/**
 * "2h ago", with the exact instant in the workspace zone as the title and the
 * dateTime. Relative text depends on the clock, so hydration differences are
 * expected and suppressed.
 */
export function RelativeTime({
  value,
  tz,
  now,
  className,
  ...props
}: Omit<ComponentProps<"time">, "dateTime" | "children"> & { value: Date | string | number; tz: string; now?: Date | string | number }) {
  const date = toDate(value);
  return (
    <time
      data-slot="relative-time"
      dateTime={date.toISOString()}
      title={`${formatInZone(date, tz, "datetime")} ${zoneAbbreviation(date, tz)} (${tz})`}
      className={cn("whitespace-nowrap", className)}
      suppressHydrationWarning
      {...props}
    >
      {relativeAge(date, now)}
    </time>
  );
}
