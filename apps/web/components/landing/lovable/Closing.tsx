"use client";

import { ArrowRight } from "./icons";
import { Button } from "@/components/ui/button";
import { Reveal } from "./Reveal";
import { Wordmark, navLinks } from "./Nav";
import { principles } from "./Principles";

export function FinalCta() {
  return (
    <section className="px-5 pb-24 sm:px-8">
      <Reveal className="relative mx-auto max-w-7xl overflow-hidden rounded-2xl bg-ink px-6 py-16 text-center text-background sm:px-12 sm:py-24">
        <div className="pointer-events-none absolute -top-32 left-1/2 h-64 w-[36rem] -translate-x-1/2 rounded-full bg-gradient-accent opacity-40 blur-3xl" aria-hidden />
        <h2 className="relative mx-auto max-w-3xl font-display text-4xl leading-[1.05] sm:text-6xl">Every account its own voice. Every post a human&apos;s call.</h2>
        <p className="relative mx-auto mt-5 max-w-xl text-background/70">Set up your workspace, connect your feeds and accounts, and keep the final say.</p>
        <div className="relative mt-9 flex flex-wrap justify-center gap-3">
          <Button asChild size="lg" variant="secondary" className="group"><a href="/register">Launch console <ArrowRight className="transition-transform group-hover:translate-x-0.5" /></a></Button>
          <Button asChild size="lg" variant="ghost" className="text-background hover:bg-background/10 hover:text-background"><a href="/login">Sign in</a></Button>
        </div>
      </Reveal>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto grid max-w-7xl gap-10 px-5 py-14 sm:px-8 md:grid-cols-[1.4fr_1fr_1fr]">
        <div><Wordmark /><p className="mt-4 max-w-xs text-sm text-muted-foreground">Self-hosted multi-agent social publishing with human approval at its core.</p></div>
        <nav aria-label="Footer">
          <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Navigate</p>
          <ul className="mt-4 space-y-2 text-sm">
            {navLinks.map((l) => <li key={l.href}><a href={l.href} className="text-ink hover:text-primary">{l.label}</a></li>)}
            <li><a href="/login" className="text-ink hover:text-primary">Sign in</a></li>
            <li><a href="/register" className="text-ink hover:text-primary">Launch console</a></li>
          </ul>
        </nav>
        <div>
          <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Principles</p>
          <ul className="mt-4 space-y-2 text-sm text-ink">{principles.map((p) => <li key={p.t}>{p.t}</li>)}</ul>
        </div>
      </div>
      <div className="border-t border-border"><p className="mx-auto max-w-7xl px-5 py-6 font-mono text-[11px] text-muted-foreground sm:px-8">© {new Date().getFullYear()} Mera</p></div>
      <div className="overflow-hidden px-5 pb-5 sm:px-8" aria-hidden="true">
        <p className="mx-auto max-w-7xl select-none text-center font-display text-[7rem] leading-[0.7] text-muted-foreground/20 sm:text-[12rem] md:text-[16rem]">mera</p>
      </div>
    </footer>
  );
}

