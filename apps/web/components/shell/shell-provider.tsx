"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ScreenId } from "@/lib/nav";
import type { ShellUser, ShellWorkspace } from "@/lib/workspace";

/** Live rail count badges, by screen. Screens absent here fall back to lib/demo.ts. */
export type NavCounts = Readonly<Partial<Record<ScreenId, number>>>;

interface ShellState {
  /** The workspace in the URL, resolved from the user's own list. */
  workspace: ShellWorkspace;
  /** Every workspace the user belongs to (GET /workspaces). */
  workspaces: readonly ShellWorkspace[];
  user: ShellUser;
  /** Live rail counts from the layout (e.g. connected accounts). */
  counts: NavCounts;
  railOpen: boolean;
  setRailOpen: (open: boolean) => void;
  paletteOpen: boolean;
  setPaletteOpen: (open: boolean) => void;
}

const ShellContext = createContext<ShellState | null>(null);
const NO_COUNTS: NavCounts = {};

export function useShell(): ShellState {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error("useShell must be used inside <ShellProvider>");
  return ctx;
}

export function ShellProvider({
  workspace,
  workspaces,
  user,
  counts = NO_COUNTS,
  children,
}: {
  workspace: ShellWorkspace;
  workspaces: readonly ShellWorkspace[];
  user: ShellUser;
  counts?: NavCounts;
  children: ReactNode;
}) {
  const [railOpen, setRailOpenState] = useState(false);
  const [paletteOpen, setPaletteOpenState] = useState(false);

  const setRailOpen = useCallback((open: boolean) => setRailOpenState(open), []);
  const setPaletteOpen = useCallback((open: boolean) => setPaletteOpenState(open), []);

  // ⌘K / Ctrl+K opens the palette from anywhere (mera.js buildPalette).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpenState((open) => !open);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const value = useMemo(
    () => ({ workspace, workspaces, user, counts, railOpen, setRailOpen, paletteOpen, setPaletteOpen }),
    [workspace, workspaces, user, counts, railOpen, setRailOpen, paletteOpen, setPaletteOpen],
  );

  return <ShellContext.Provider value={value}>{children}</ShellContext.Provider>;
}
