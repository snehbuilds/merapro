"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { Newspaper } from "./icons";
import { Reveal, SectionHeading } from "./Reveal";
import { cn } from "@/lib/utils";

const voices = [
  { id: "linkedin", name: "LinkedIn", handle: "@talonic", tone: "Analytical, operator-focused", limit: "Long-form",
    body: "The first national framework for grid-scale battery storage landed this week. For mid-size utilities, three things change immediately:\n\n1. Interconnection studies get a fixed 120-day window\n2. Storage counts toward capacity obligations\n3. Co-located solar + storage gets a single permit path\n\nWe've summarised what it means for planning cycles in 2027.",
    tags: ["#EnergyStorage", "#GridModernization"] },
  { id: "x", name: "X", handle: "@talonic", tone: "Punchy, thread opener", limit: "≤ 280 chars",
    body: "Grid-scale storage finally has a rulebook.\n\nFixed 120-day interconnection studies. Storage counts toward capacity. One permit path for solar + storage.\n\nWhat it means for utilities 👇",
    tags: ["#energy"] },
  { id: "threads", name: "Threads", handle: "@talonic", tone: "Conversational", limit: "Casual",
    body: "Not the flashiest headline this week, but probably the one that matters most for the grid: batteries now have clear rules. The single permit path for solar + storage is the sleeper detail here.",
    tags: [] },
  { id: "bluesky", name: "Bluesky", handle: "@talonic", tone: "Plain, source-first", limit: "≤ 300 chars",
    body: "New federal framework for grid-scale battery storage: fixed interconnection timelines, capacity credit for storage, and a combined permit path for co-located solar. Full text linked from the regulator's notice.",
    tags: [] },
];

export function Voices() {
  const [active, setActive] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const v = voices[active] ?? voices[0]!;
  const onKey = (e: KeyboardEvent) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = (active + d + voices.length) % voices.length;
    setActive(n); tabs.current[n]?.focus();
  };
  return (
    <section id="product" className="mx-auto max-w-7xl px-5 py-24 sm:px-8 lg:py-32">
      <SectionHeading eyebrow="One source, distinct voices" title={<>Not cross-posting. <span className="text-primary">Re-authoring.</span></>}
        body="Every target account gets its own post set, written for its platform, audience and tone. The same researched story never goes out as a blind duplicate." />
      <Reveal delay={120} className="mt-14 grid gap-6 lg:grid-cols-[320px_1fr]">
        <div className="rounded-xl border border-border bg-surface p-5">
          <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-primary"><Newspaper className="h-4 w-4" /> Source story</div>
          <p className="mt-3 font-display text-2xl leading-tight text-ink">Regulator publishes first framework for grid-scale battery storage</p>
          <p className="mt-3 text-sm text-muted-foreground">Picked up from a monitored industry feed. Researched, cross-checked and handed to the Writer agent as one brief.</p>
          <div role="tablist" aria-label="Target platform" className="mt-6 grid grid-cols-2 gap-1.5" onKeyDown={onKey}>
            {voices.map((x, i) => (
              <button key={x.id} ref={(el) => { tabs.current[i] = el; }} role="tab" id={`tab-${x.id}`} aria-selected={i === active} aria-controls="voice-panel" tabIndex={i === active ? 0 : -1}
                onClick={() => setActive(i)}
                className={cn("rounded-md border px-3 py-2 text-left text-sm transition-all", i === active ? "border-primary bg-card text-ink shadow-card" : "border-transparent text-muted-foreground hover:bg-card/60 hover:text-ink")}>
                {x.name}
              </button>
            ))}
          </div>
        </div>
        <div id="voice-panel" role="tabpanel" aria-labelledby={`tab-${v.id}`} className="rounded-xl border border-border bg-card p-6 shadow-card sm:p-8">
          <div key={v.id} className="animate-swap">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5">
              <div className="flex items-center gap-3">
                <span className="grid h-10 w-10 place-items-center rounded-full bg-ink font-display text-lg text-background">U</span>
                <div><p className="text-sm font-semibold text-ink">{v.handle}</p><p className="text-xs text-muted-foreground">{v.name} post set · draft v1</p></div>
              </div>
              <div className="flex gap-2 font-mono text-[11px]">
                <span className="rounded bg-accent px-2 py-1 text-accent-foreground">{v.tone}</span>
                <span className="rounded border border-border px-2 py-1 text-muted-foreground">{v.limit}</span>
              </div>
            </div>
            <p className="mt-6 whitespace-pre-line text-[15px] leading-relaxed text-ink">{v.body}</p>
            {v.tags.length > 0 && <p className="mt-4 text-sm text-primary">{v.tags.join(" ")}</p>}
            <p className="mt-8 font-mono text-[11px] text-muted-foreground">{v.body.length} characters · unique to this account · awaiting approval</p>
          </div>
        </div>
      </Reveal>
    </section>
  );
}

