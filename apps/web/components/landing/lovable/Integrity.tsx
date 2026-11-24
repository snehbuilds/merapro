"use client";

import { useMemo, useState } from "react";
import { ShieldCheck, ShieldX, RotateCcw, Shuffle } from "./icons";
import { Button } from "@/components/ui/button";
import { Reveal } from "./Reveal";
import { cn } from "@/lib/utils";

const ORIGINAL = "Grid-scale storage finally has a rulebook. Fixed 120-day interconnection studies and one permit path for solar + storage.";
const ACCOUNT = "@usemera";

/** Illustrative non-cryptographic hash (FNV-1a variant) for the front-end demo only. */
export function demoHash(s: string) {
  let out = "";
  for (let seed = 0; seed < 4; seed++) {
    let h = 0x811c9dc5 ^ seed;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    out += h.toString(16).padStart(8, "0");
  }
  return out;
}

export function Integrity() {
  const [text, setText] = useState(ORIGINAL);
  const signed = useMemo(() => demoHash(ACCOUNT + "|v3|" + ORIGINAL), []);
  const current = demoHash(ACCOUNT + "|v3|" + text);
  const valid = signed === current;
  const mutate = () => {
    const i = Math.floor(Math.random() * text.length);
    const c = text[i] === "e" ? "a" : "e";
    setText(text.slice(0, i) + c + text.slice(i + 1));
  };
  return (
    <section className="lovable-inverse py-24 lg:py-32" aria-labelledby="integrity-title">
      <div className="mx-auto grid max-w-7xl gap-14 px-5 sm:px-8 lg:grid-cols-2">
        <div>
          <Reveal>
            <p className="font-mono text-xs uppercase tracking-[0.18em] text-accent">Approval integrity</p>
            <h2 id="integrity-title" className="mt-4 font-display text-4xl leading-[1.05] sm:text-5xl md:text-6xl">Change one character. The approval is gone.</h2>
            <p className="mt-5 text-lg leading-relaxed text-background/70">Approval tokens are immutable and cryptographically signed, bound to the exact content version and destination account. Any edit produces a new version that needs a fresh sign-off.</p>
          </Reveal>
          <Reveal delay={120} as="ul" className="mt-8 space-y-3 text-sm text-background/80">
            {["No timeout-to-publish — an expired approval terminates, it never auto-ships.", "Tokens can't be reused on another account or another version.", "The publisher re-verifies the token immediately before the API call."].map((s) => (
              <li key={s} className="flex gap-3"><span className="mt-2 h-1 w-3 shrink-0 bg-accent" />{s}</li>
            ))}
          </Reveal>
        </div>
        <Reveal delay={160} className="rounded-xl border border-background/15 bg-background p-5 text-foreground sm:p-6">
          <div className="flex items-center justify-between">
            <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Interactive demo</p>
            <span className="rounded bg-warning/15 px-2 py-0.5 font-mono text-[10px] text-ink">Illustrative simulation</span>
          </div>
          <label htmlFor="demo-post" className="mt-4 block text-sm font-medium text-ink">Post text for {ACCOUNT} · v3</label>
          <textarea id="demo-post" value={text} onChange={(e) => setText(e.target.value)} rows={4}
            className="mt-2 w-full resize-none rounded-md border border-input bg-card p-3 text-sm leading-relaxed text-ink focus-visible:outline-2" />
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={mutate}><Shuffle /> Change a character</Button>
            <Button size="sm" variant="ghost" onClick={() => setText(ORIGINAL)} disabled={valid}><RotateCcw /> Restore approved text</Button>
          </div>
          <dl className="mt-5 space-y-2 rounded-md bg-muted p-3 font-mono text-[11px]">
            <div className="flex gap-3"><dt className="w-20 shrink-0 text-muted-foreground">signed</dt><dd className="break-all text-ink">{signed}</dd></div>
            <div className="flex gap-3"><dt className="w-20 shrink-0 text-muted-foreground">current</dt><dd className={cn("break-all", valid ? "text-ink" : "text-destructive")}>{current}</dd></div>
          </dl>
          <div role="status" aria-live="polite" className={cn("mt-4 flex items-center gap-3 rounded-md border p-3 transition-colors", valid ? "border-success/40 bg-success/10" : "border-destructive/40 bg-destructive/10")}>
            {valid ? <ShieldCheck className="h-5 w-5 text-success" /> : <ShieldX className="h-5 w-5 text-destructive" />}
            <div>
              <p className="text-sm font-semibold text-ink">{valid ? "Approval valid" : "Approval revoked"}</p>
              <p className="text-xs text-muted-foreground">{valid ? "Content matches the signed version. Publisher may proceed." : "Content no longer matches. A new approval request is required."}</p>
            </div>
          </div>
          <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">This runs entirely in your browser with a simple non-cryptographic hash to illustrate the behaviour. It is not Mera&apos;s real signature scheme or verification.</p>
        </Reveal>
      </div>
    </section>
  );
}

