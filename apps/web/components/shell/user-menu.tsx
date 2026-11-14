"use client";

import Link from "next/link";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { SendVerification } from "@/components/auth/send-verification";
import { Icon } from "@/components/icons";
import { sessionLogout } from "@/lib/api/browser";
import { cn } from "@/lib/utils";
import { roleLabel } from "@/lib/workspace";
import { useShell } from "./shell-provider";

/*
 * The rail's user row (mera.css .rail__foot .user), made a menu button: the
 * signed-in user, their role in this workspace, and Sign out. Opens upwards
 * because it sits at the foot of the rail. WAI-ARIA menu button: Enter/Space/
 * ArrowUp/ArrowDown open and focus the first item; Esc closes and returns
 * focus; Tab closes.
 */
export function UserMenu() {
  const { user, workspace } = useShell();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (open) rootRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onPointer(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  function close(restoreFocus: boolean) {
    setOpen(false);
    if (restoreFocus) buttonRef.current?.focus();
  }

  function onButtonKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
    }
  }

  function onMenuKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      close(true);
    } else if (e.key === "Tab") {
      setOpen(false);
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])'));
      if (items.length === 0) return;
      const at = items.indexOf(document.activeElement as HTMLElement);
      const next =
        e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : e.key === "ArrowDown" ? (at + 1) % items.length : (at - 1 + items.length) % items.length;
      items[next]?.focus();
    }
  }

  async function signOut() {
    setBusy(true);
    setFailed(false);
    const result = await sessionLogout();
    if (!result.ok) {
      // The cookies are cleared server-side whatever the API said; only a
      // network failure lands here.
      setBusy(false);
      setFailed(true);
      return;
    }
    router.replace("/login");
    router.refresh();
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`${user.name}, ${roleLabel(workspace.role)}. Account menu`}
        onClick={() => (open ? close(false) : setOpen(true))}
        onKeyDown={onButtonKey}
        className="flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-left text-caption text-rail-fg hover:bg-rail-hover hover:text-rail-hi"
      >
        <b
          aria-hidden="true"
          className="grid size-[22px] shrink-0 place-items-center rounded-full bg-rail-active text-[10.5px] font-semibold text-rail-hi"
        >
          {user.initials}
        </b>
        <span className="min-w-0 truncate">{user.name}</span>
        <span className="ml-auto font-label text-2xs tracking-[0.08em] text-rail-subtle uppercase">
          {roleLabel(workspace.role)}
        </span>
      </button>

      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label="Account"
          onKeyDown={onMenuKey}
          className="absolute inset-x-0 bottom-full z-30 mb-1 grid gap-0.5 rounded-md border border-rail-border bg-rail-bg p-1 shadow-popover"
        >
          <div className="flex items-center gap-2 px-2 pt-1 pb-1.5">
            <span className="min-w-0 truncate font-mono text-2xs text-rail-subtle" title={user.email}>
              {user.email}
            </span>
            {user.emailVerified ? null : (
              <span className="ml-auto shrink-0 rounded-sm border border-rail-border px-1.5 font-label text-2xs tracking-[0.08em] text-rail-fg uppercase">
                Unverified
              </span>
            )}
          </div>
          {user.emailVerified ? null : <SendVerification surface="rail" />}
          <Link
            href={`/w/${workspace.slug}/security`}
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex h-7 items-center gap-2.5 rounded-md px-2 text-left text-body text-rail-fg hover:bg-rail-active hover:text-rail-hi focus-visible:bg-rail-active focus-visible:text-rail-hi"
          >
            <Icon name="settings" size="sm" className="text-rail-subtle" />
            Your security
          </Link>
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={signOut}
            className={cn(
              "flex h-7 items-center gap-2.5 rounded-md px-2 text-left text-body text-rail-fg",
              "hover:bg-rail-active hover:text-rail-hi focus-visible:bg-rail-active focus-visible:text-rail-hi",
              "disabled:opacity-55",
            )}
          >
            <Icon name="arrow" size="sm" className="text-rail-subtle" />
            {busy ? "Signing out…" : "Sign out"}
          </button>
          {failed ? (
            <p role="alert" className="px-2 pb-1 text-2xs text-rail-subtle">
              Couldn&apos;t reach Mera. Try again.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
