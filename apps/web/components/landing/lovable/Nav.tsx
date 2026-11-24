"use client";

import { useEffect, useState } from "react";
import { Menu, X } from "./icons";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const navLinks = [
  { href: "#product", label: "Product" },
  { href: "#architecture", label: "Architecture" },
  { href: "#security", label: "Security" },
];

export function Wordmark() {
  return (
    <a href="#top" className="flex items-center gap-2" aria-label="Mera home">
      <span className="grid h-7 w-7 place-items-center rounded-md bg-ink text-background">
        <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden><path d="M4 18V6l8 7 8-7v12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </span>
      <span className="font-display text-2xl leading-none text-ink">Mera</span>
    </a>
  );
}

export function Nav() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on(); window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className={cn("sticky top-0 z-50 border-b transition-colors", scrolled ? "border-border bg-background/85 backdrop-blur-md" : "border-transparent bg-background")}>
      <nav className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 sm:px-8" aria-label="Main">
        <div className="flex items-center gap-10">
          <Wordmark />
          <ul className="hidden items-center gap-7 md:flex">
            {navLinks.map((l) => (
              <li key={l.href}><a href={l.href} className="text-sm text-muted-foreground transition-colors hover:text-ink">{l.label}</a></li>
            ))}
          </ul>
        </div>
        <div className="hidden items-center gap-2 md:flex">
          <Button asChild variant="ghost" size="sm"><a href="/login">Sign in</a></Button>
          <Button asChild size="sm"><a href="/register">Launch console</a></Button>
        </div>
        <button className="rounded-md p-2 md:hidden" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Toggle menu">
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </nav>
      {open && (
        <div className="border-t border-border bg-background px-5 pb-5 md:hidden">
          <ul className="flex flex-col py-2">
            {navLinks.map((l) => (
              <li key={l.href}><a href={l.href} onClick={() => setOpen(false)} className="block py-3 text-ink">{l.label}</a></li>
            ))}
          </ul>
          <div className="flex gap-2">
            <Button asChild variant="outline" className="flex-1"><a href="/login">Sign in</a></Button>
            <Button asChild className="flex-1"><a href="/register">Launch console</a></Button>
          </div>
        </div>
      )}
    </header>
  );
}

