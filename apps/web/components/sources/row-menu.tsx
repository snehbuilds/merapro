"use client";

import { useEffect, useId, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/*
 * The table row's "More" disclosure (sources.html): a ghost icon button that
 * opens a short list of actions. A disclosure of plain buttons, not an ARIA
 * menu, as with the topbar's Connect menu: Tab moves through the actions,
 * Escape closes and returns focus, a click outside closes.
 */
export interface RowMenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

export function RowMenu({ label, items, disabled }: { label: string; items: readonly RowMenuItem[]; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const firstRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    firstRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    function onPointer(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  const firstEnabled = items.findIndex((i) => !i.disabled);
  return (
    <div ref={rootRef} className="relative inline-block">
      <Button
        ref={triggerRef}
        variant="ghost"
        size="icon-sm"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name="more" size="sm" />
      </Button>
      {open ? (
        <div
          id={panelId}
          role="group"
          aria-label={label}
          className="absolute top-[calc(100%+4px)] right-0 z-30 w-44 rounded-md border border-border bg-surface-raised p-1 text-left shadow-popover"
        >
          <ul>
            {items.map((item, i) => (
              <li key={item.label}>
                <button
                  ref={i === firstEnabled ? firstRef : undefined}
                  type="button"
                  disabled={item.disabled}
                  onClick={() => {
                    setOpen(false);
                    item.onSelect();
                  }}
                  className={cn(
                    "flex h-8 w-full items-center rounded-sm px-2 text-left text-sm text-fg",
                    "hover:bg-surface-hover disabled:cursor-not-allowed disabled:text-fg-subtle disabled:hover:bg-transparent",
                    item.danger && "text-danger",
                  )}
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
