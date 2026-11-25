"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
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
    <a
      href="#top"
      className="inline-flex items-center md:rounded-lg md:bg-white/95 md:px-2.5 md:py-1 md:shadow-[0_1px_0_rgba(255,255,255,.7),0_8px_24px_-16px_rgba(0,70,255,.8)] md:ring-1 md:ring-slate-900/5"
      aria-label="Talonic home"
    >
      <Image
        src="/brand/talonic-mark.png"
        alt=""
        width={48}
        height={48}
        className="h-9 w-9 object-contain md:hidden"
      />
      <Image
        src="/brand/talonic-wordmark.png"
        alt="Talonic"
        width={326}
        height={109}
        preload
        className="hidden h-8 w-auto md:block"
      />
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

