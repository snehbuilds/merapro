"use client";

import { useEffect, useRef, useState, type ReactNode, type ElementType } from "react";
import { cn } from "@/lib/utils";

export function useInView<T extends Element>(threshold = 0.2) {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => { if (e?.isIntersecting) { setInView(true); io.disconnect(); } },
      { threshold },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return { ref, inView };
}

export function Reveal({ children, delay = 0, className, as: Tag = "div" }: { children: ReactNode; delay?: number; className?: string; as?: ElementType }) {
  const { ref, inView } = useInView<HTMLDivElement>(0.15);
  return (
    <Tag ref={ref} className={cn("reveal", inView && "is-visible", className)} style={{ ["--d" as string]: `${delay}ms` }}>
      {children}
    </Tag>
  );
}

export function SectionHeading({ eyebrow, title, body, center }: { eyebrow: string; title: ReactNode; body?: ReactNode; center?: boolean }) {
  return (
    <Reveal className={cn("max-w-2xl", center && "mx-auto text-center")}>
      <p className="font-mono text-xs uppercase tracking-[0.18em] text-primary">{eyebrow}</p>
      <h2 className="mt-4 font-display text-4xl leading-[1.05] text-ink sm:text-5xl md:text-6xl">{title}</h2>
      {body && <p className="mt-5 text-base leading-relaxed text-muted-foreground sm:text-lg">{body}</p>}
    </Reveal>
  );
}

