"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Icon } from "@/components/icons";
import { screenFromPath, screenHref } from "@/lib/nav";
import { cn } from "@/lib/utils";
import type { ShellWorkspace } from "@/lib/workspace";

/*
 * mera.css .ws, made real: a button that opens a single-select listbox
 * (WAI-ARIA "select-only combobox" pattern simplified to button + listbox).
 * Keys: Enter/Space/ArrowDown/ArrowUp open; arrows/Home/End move; Enter/Space
 * choose; Esc closes and returns focus; Tab closes.
 */
export function WorkspaceSwitcher({
  current,
  workspaces,
}: {
  current: ShellWorkspace;
  workspaces: readonly ShellWorkspace[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const optionId = (i: number) => `${listId}-opt-${i}`;
  const currentIndex = Math.max(
    0,
    workspaces.findIndex((w) => w.slug === current.slug),
  );

  function openList(index = currentIndex) {
    setActive(index);
    setOpen(true);
  }

  function close(restoreFocus: boolean) {
    setOpen(false);
    if (restoreFocus) buttonRef.current?.focus();
  }

  function choose(ws: ShellWorkspace | undefined) {
    close(true);
    if (!ws || ws.slug === current.slug) return;
    router.push(screenHref(ws.slug, screenFromPath(pathname) ?? "dashboard"));
  }

  // Focus the listbox when it opens; it manages the active option itself.
  useEffect(() => {
    if (open) listRef.current?.focus();
  }, [open]);

  // Click outside closes without stealing focus.
  useEffect(() => {
    if (!open) return;
    function onPointer(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  function onButtonKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openList();
    }
  }

  function onListKey(e: KeyboardEvent<HTMLUListElement>) {
    const last = workspaces.length - 1;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => Math.min(i + 1, last));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(last);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        choose(workspaces[active]);
        break;
      case "Escape":
        e.preventDefault();
        close(true);
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`Workspace: ${current.name}. Switch workspace`}
        onClick={() => (open ? close(false) : openList())}
        onKeyDown={onButtonKey}
        className="flex w-full items-center gap-2.5 rounded-md border border-rail-border p-2 text-left text-rail-fg hover:bg-rail-hover"
      >
        <WorkspaceMark ws={current} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-body leading-[1.3] font-semibold text-rail-hi">{current.name}</span>
          <span className="block truncate text-xs text-rail-subtle">{current.meta}</span>
        </span>
        <Icon name="chevdown" size="sm" className="text-rail-subtle" />
      </button>

      {open ? (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-label="Workspaces"
          aria-activedescendant={optionId(active)}
          onKeyDown={onListKey}
          className="absolute inset-x-0 top-full z-30 mt-1 grid gap-0.5 rounded-md border border-rail-border bg-rail-bg p-1 shadow-popover outline-none focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          {workspaces.map((ws, i) => {
            const selected = ws.slug === current.slug;
            return (
              // Keyboard handling lives on the listbox (aria-activedescendant pattern).
              <li
                key={ws.slug}
                id={optionId(i)}
                role="option"
                aria-selected={selected}
                onPointerMove={() => setActive(i)}
                onClick={() => choose(ws)}
                className={cn(
                  "flex cursor-pointer items-center gap-2.5 rounded-md p-1.5 text-rail-fg",
                  i === active && "bg-rail-active text-rail-hi",
                )}
              >
                <WorkspaceMark ws={ws} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body leading-[1.3] font-semibold text-rail-hi">{ws.name}</span>
                  <span className="block truncate text-xs text-rail-subtle">{ws.meta}</span>
                </span>
                {selected ? <Icon name="check" size="sm" className="text-rail-accent" /> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function WorkspaceMark({ ws }: { ws: ShellWorkspace }) {
  return (
    <span
      aria-hidden="true"
      className="grid size-[26px] shrink-0 place-items-center rounded-sm bg-rail-ws-mark-bg font-label text-mono-xs font-semibold tracking-[0.02em] text-rail-ws-mark-fg"
    >
      {ws.mark}
    </span>
  );
}
