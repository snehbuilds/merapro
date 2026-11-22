"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { Icon } from "@/components/icons";
import { MOBILE_RAIL_QUERY, useMediaQuery } from "@/hooks/use-media-query";
import { navCounts } from "@/lib/demo";
import { screenFromPath, screenHref, visibleNav } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { KillSwitch } from "./kill-switch";
import { useShell } from "./shell-provider";
import { UserMenu } from "./user-menu";
import { WorkspaceSwitcher } from "./workspace-switcher";

export const RAIL_ID = "app-rail";

/*
 * mera.js renderRail + mera.css .rail: an always-dark control rail. At ≤900px
 * it goes off-canvas; while closed there it is `inert` so its links are not
 * tab stops behind the page.
 */
export function Rail() {
  const { workspace, workspaces, counts, railOpen, setRailOpen } = useShell();
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
          // The rail's own controls need their own focus colour on the dark surface.
          "sticky top-0 flex h-screen flex-col bg-rail-bg text-rail-fg [--focus:var(--rail-focus)]",
          "max-rail:fixed max-rail:inset-y-0 max-rail:left-0 max-rail:z-60 max-rail:w-(--rail-w) max-rail:-translate-x-full",
          "max-rail:transition-transform max-rail:duration-[180ms] max-rail:ease-[cubic-bezier(.2,.8,.3,1)]",
          "max-rail:data-[open=true]:translate-x-0 max-rail:data-[open=true]:shadow-popover",
        )}
      >
        <div className="flex h-(--topbar-h) shrink-0 items-center gap-2 border-b border-rail-border px-4 font-label text-md font-semibold tracking-[0.14em] text-rail-hi uppercase">
          <i aria-hidden="true" className="size-[9px] shrink-0 rotate-45 rounded-[1px] bg-rail-accent" />
          Mera
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
                  // Live counts from the layout; DEMO counts (lib/demo.ts) for screens whose endpoint hasn't landed.
                  const count = item.id in counts ? counts[item.id] : navCounts[item.id];
                  return (
                    <li key={item.id}>
                      <Link
                        href={screenHref(workspace.slug, item.id)}
                        data-slot="nav-item"
                        aria-current={isHere ? "page" : undefined}
                        onClick={() => setRailOpen(false)}
                        className={cn(
                          "group relative flex h-7 items-center gap-2.5 rounded-md px-2 text-body text-rail-fg",
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
          <KillSwitch />
        </div>
      </aside>
    </>
  );
}
