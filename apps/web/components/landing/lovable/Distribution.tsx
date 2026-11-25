"use client";

import { Reveal, SectionHeading, useInView } from "./Reveal";
import { cn } from "@/lib/utils";

const rows = [
  { acct: "@talonic · LinkedIn", at: 0 },
  { acct: "@talonic · X", at: 18 },
  { acct: "@talonic · Threads", at: 37 },
  { acct: "@talonic · Bluesky", at: 59 },
  { acct: "@talonic · X", at: 82 },
];

export function Distribution() {
  const { ref, inView } = useInView<HTMLDivElement>(0.3);
  return (
    <section className="border-y border-border bg-card py-24 lg:py-32">
      <div className="mx-auto grid max-w-7xl gap-14 px-5 sm:px-8 lg:grid-cols-[1fr_1.3fr] lg:items-center">
        <SectionHeading eyebrow="Staggered distribution" title="Five accounts. Never all at once."
          body="Approved posts are released across accounts at staggered offsets with adaptive jitter, so a network of accounts reads like independent voices instead of a synchronised burst." />
        <Reveal delay={120}>
          <div ref={ref} className="rounded-xl border border-border bg-background p-5 sm:p-6">
            <div className="flex justify-between font-mono text-[10px] text-muted-foreground"><span>T+0 min</span><span>T+45</span><span>T+90</span></div>
            <ul className="mt-3 space-y-3">
              {rows.map((r, i) => (
                <li key={`${r.acct}-${r.at}`} className="grid grid-cols-[minmax(0,9rem)_1fr] items-center gap-3 sm:grid-cols-[12rem_1fr]">
                  <span className="truncate text-xs text-ink sm:text-sm">{r.acct}</span>
                  <div className="relative h-7 rounded bg-muted">
                    <div className={cn("absolute inset-y-0 left-0 origin-left rounded bg-accent transition-transform duration-[1400ms] ease-out", inView ? "scale-x-100" : "scale-x-0")}
                      style={{ width: `${r.at}%`, transitionDelay: `${i * 260}ms` }} />
                    <span className={cn("absolute top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1 rounded-full bg-primary px-2 py-0.5 font-mono text-[10px] text-primary-foreground shadow-card transition-all duration-500", inView ? "scale-100 opacity-100" : "scale-75 opacity-0")}
                      style={{ left: `${Math.max(r.at, 6)}%`, transitionDelay: `${i * 260 + 1200}ms` }}>
                      +{Math.round(r.at * 0.9)}m
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-5 font-mono text-[11px] text-muted-foreground">Example offsets. Actual timing is set by the Scheduler per workspace.</p>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

