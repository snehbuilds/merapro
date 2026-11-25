"use client";

import Link from "next/link";
import { useSyncExternalStore, type ReactNode } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { screenHref, type ScreenId } from "@/lib/nav";
import { RAIL_ID } from "./rail";
import { useShell } from "./shell-provider";

export interface TopbarProps {
  title: string;
  sub?: string;
  /**
   * Primary action. With `target` it links to that screen, with `href` to that
   * URL (e.g. an in-page "#invite"); with neither it is a disabled stub.
   */
  action?: { label: string; target?: ScreenId; href?: string };
  /** A custom primary control (e.g. the Connect menu) in place of `action`. */
  actionNode?: ReactNode;
}

const noopSubscribe = () => () => {};
function useIsMac() {
  return useSyncExternalStore(
    noopSubscribe,
    () => /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent),
    // The mockup shows ⌘; keep that for SSR so the common case does not shift.
    () => true,
  );
}

/* mera.js renderTopbar + mera.css .topbar */
export function Topbar({ title, sub, action, actionNode }: TopbarProps) {
  const { workspace, railOpen, setRailOpen, setPaletteOpen } = useShell();
  const isMac = useIsMac();

  return (
    <header
      data-slot="topbar"
      className="sticky top-0 z-20 flex h-(--topbar-h) shrink-0 items-center gap-3 border-b border-border bg-background/85 px-6 backdrop-blur-md max-rail:gap-2 max-rail:px-3"
    >
      <Button
        variant="ghost"
        size="icon"
        className="hidden max-rail:inline-flex"
        aria-label="Open navigation"
        aria-controls={RAIL_ID}
        aria-expanded={railOpen}
        onClick={() => setRailOpen(!railOpen)}
      >
        <Icon name="menu" />
      </Button>

      <span className="font-label text-xs font-medium tracking-[0.08em] whitespace-nowrap text-fg-subtle uppercase after:ml-2.5 after:text-border-strong after:content-['/'] max-rail:hidden">
        {workspace.name}
      </span>

      <div className="flex min-w-0 flex-[0_1_auto] items-baseline gap-2.5">
        <h1 className="min-w-0 shrink-0 truncate font-display text-2xl leading-none text-ink max-narrow:text-xl">{title}</h1>
        {sub ? (
          <span className="min-w-0 shrink truncate font-mono text-xs whitespace-nowrap text-fg-subtle max-rail:hidden">{sub}</span>
        ) : null}
      </div>

      <div className="flex-1" />

      <button
        type="button"
        data-slot="cmdk"
        onClick={() => setPaletteOpen(true)}
        aria-haspopup="dialog"
        aria-keyshortcuts={isMac ? "Meta+K" : "Control+K"}
        className="flex h-8 w-60 shrink-0 items-center gap-2 rounded-lg border border-border bg-card pr-[7px] pl-2.5 text-sm text-fg-subtle shadow-card hover:border-primary/40 hover:text-primary max-wide:w-45 max-rail:w-8 max-rail:justify-center max-rail:p-0"
      >
        <Icon name="search" size="sm" />
        <span className="flex-1 text-left max-rail:hidden">Search</span>
        <span className="flex gap-0.5 max-rail:hidden" aria-hidden="true">
          <Kbd>{isMac ? "⌘" : "Ctrl"}</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>

      {actionNode ?? null}

      {!actionNode && action ? (
        action.target || action.href ? (
          <Button asChild variant="primary" className="shrink-0 max-narrow:w-8 max-narrow:px-0">
            <Link href={action.href ?? screenHref(workspace.slug, action.target!)}>
              <Icon name="plus" size="sm" />
              <span className="max-narrow:sr-only">{action.label}</span>
            </Link>
          </Button>
        ) : (
          <Button
            variant="primary"
            className="shrink-0 max-narrow:w-8 max-narrow:px-0"
            disabled
            title="Not built yet — see docs/12 §4"
          >
            <Icon name="plus" size="sm" />
            <span className="max-narrow:sr-only">{action.label}</span>
          </Button>
        )
      ) : null}
    </header>
  );
}
