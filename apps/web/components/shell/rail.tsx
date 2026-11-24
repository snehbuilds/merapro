"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { Icon } from "@/components/icons";
import { MOBILE_RAIL_QUERY, useMediaQuery } from "@/hooks/use-media-query";
import { screenFromPath, screenHref, visibleNav } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { useShell, type NavCounts } from "./shell-provider";
import { UserMenu } from "./user-menu";
import { WorkspaceSwitcher } from "./workspace-switcher";

export const RAIL_ID = "app-rail";

/* The product shell uses the Signal To Social Studio card-and-grid language. */
export function Rail({ liveCounts }: { liveCounts?: NavCounts }) {
  const { workspace, workspaces, counts, railOpen, setRailOpen } = useShell();
  const displayedCounts = liveCounts ?? counts;
  const pathname = usePathname();
  const current = screenFromPath(pathname);
  const isMobile = useMediaQuery(MOBILE_RAIL_QUERY);
  const hidden = isMobile && !railOpen;

  // Esc closes the off-canvas rail.
  useEffect(() => {
    if (!railOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setRailOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [railOpen, setRailOpen]);

  return (
    <>
      {isMobile && railOpen ? (
        <div aria-hidden="true" className="fixed inset-0 z-50 bg-scrim" onClick={() => setRailOpen(false)} />
      ) : null}
      <aside
        id={RAIL_ID}
        data-open={railOpen}
        inert={hidden}
        aria-label="Workspace navigation"
        className={cn(
          "sticky top-0 flex h-screen flex-col border-r border-rail-border bg-rail-bg/96 text-rail-fg backdrop-blur-xl [--focus:var(--rail-focus)]",
          "max-rail:fixed max-rail:inset-y-0 max-rail:left-0 max-rail:z-60 max-rail:w-(--rail-w) max-rail:-translate-x-full",
          "max-rail:transition-transform max-rail:duration-[180ms] max-rail:ease-[cubic-bezier(.2,.8,.3,1)]",
          "max-rail:data-[open=true]:translate-x-0 max-rail:data-[open=true]:shadow-popover",
        )}
      >
        <div className="flex h-(--topbar-h) shrink-0 items-center gap-2 border-b border-rail-border px-4 text-rail-hi">
          <span className="grid size-7 place-items-center rounded-md bg-ink text-background">
            <svg viewBox="0 0 24 24" className="size-4" aria-hidden="true">
              <path d="M4 18V6l8 7 8-7v12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span className="font-display text-2xl leading-none">Mera</span>
        </div>

        <div className="px-2.5 pt-2.5 pb-1">
          <WorkspaceSwitcher current={workspace} workspaces={workspaces} />
        </div>

        <nav
          aria-label="Main"
          className="flex-1 overflow-y-auto px-2.5 pt-2 pb-3 [scrollbar-color:var(--rail-active)_transparent]"
        >
          {visibleNav().map((group) => (
            <div key={group.label} className="mt-2.5">
              <div
                id={`rail-group-${group.label.toLowerCase()}`}
                className="flex items-center gap-2 px-2 pb-1 font-label text-2xs font-semibold tracking-[0.12em] text-rail-subtle uppercase after:h-px after:flex-1 after:bg-rail-border after:content-['']">
                {group.label}
              </div>
              <ul aria-labelledby={`rail-group-${group.label.toLowerCase()}`}>
                {group.items.map((item) => {
                  const isHere = item.id === current;
                  const count = displayedCounts[item.id];
                  return (
                    <li key={item.id}>
                      <Link
                        href={screenHref(workspace.slug, item.id)}
                        data-slot="nav-item"
                        aria-current={isHere ? "page" : undefined}
                        onClick={() => setRailOpen(false)}
                        className={cn(
                          "group relative flex h-8 items-center gap-2.5 rounded-lg px-2 text-body text-rail-fg transition-colors",
                          "hover:bg-rail-hover hover:text-rail-hi",
                          "aria-[current=page]:bg-rail-active aria-[current=page]:font-medium aria-[current=page]:text-rail-hi",
                          // 3px accent bar flush with the rail edge (mera.css .navitem::before).
                          "aria-[current=page]:before:absolute aria-[current=page]:before:inset-y-[7px] aria-[current=page]:before:-left-2.5 aria-[current=page]:before:w-[3px] aria-[current=page]:before:rounded-r-[2px] aria-[current=page]:before:bg-rail-accent aria-[current=page]:before:content-['']",
                        )}
                      >
                        <Icon
                          name={item.icon}
                          className="text-rail-subtle group-hover:text-rail-hi group-aria-[current=page]:text-rail-hi"
                        />
                        {item.navLabel}
                        {count !== undefined ? (
                          <span className="tabular ml-auto min-w-[22px] rounded-[9px] bg-rail-active px-1.5 py-px text-center font-mono text-2xs text-rail-fg">
                            {count}
                          </span>
                        ) : null}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="grid gap-2 border-t border-rail-border p-2.5">
          <UserMenu />
        </div>
      </aside>
    </>
  );
}
