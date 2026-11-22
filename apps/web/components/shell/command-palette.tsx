"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";
import { Kbd } from "@/components/ui/kbd";
import { useTheme } from "@/hooks/use-theme";
import { paletteScreens, screenHref } from "@/lib/nav";
import type { ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { useShell } from "./shell-provider";

const THEME_ACTIONS: { pref: ThemePreference; label: string }[] = [
  { pref: "system", label: "Theme: follow system" },
  { pref: "light", label: "Theme: light" },
  { pref: "dark", label: "Theme: dark" },
];

const groupClass =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-1.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:font-label [&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-[0.12em] [&_[cmdk-group-heading]]:text-fg-subtle [&_[cmdk-group-heading]]:uppercase";

const itemClass =
  "grid cursor-pointer gap-px rounded-md px-2 py-2 outline-none data-[selected=true]:bg-surface-active";

/*
 * mera.js buildPalette, rebuilt on Radix Dialog (focus trap, Esc, focus return,
 * aria-modal) + cmdk (filtering, listbox semantics, arrow keys).
 * Screens first, then actions (docs/12 F1.8). "Approve next" and "Stop
 * automation" land with their features (M3 / M6.12).
 */
export function CommandPalette() {
  const { workspace, paletteOpen, setPaletteOpen } = useShell();
  const { preference, setPreference } = useTheme();
  const router = useRouter();

  function go(href: string) {
    setPaletteOpen(false);
    router.push(href);
  }

  return (
    <Dialog.Root open={paletteOpen} onOpenChange={setPaletteOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-200 bg-scrim" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed top-[14vh] left-1/2 z-200 w-[min(540px,92vw)] -translate-x-1/2 overflow-hidden rounded-lg border border-border-strong bg-surface-raised text-fg shadow-popover"
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Command label="Command palette" loop>
            <div className="flex items-center gap-2 border-b border-border p-3">
              <Icon name="search" className="text-fg-subtle" />
              <Command.Input
                placeholder="Go to screen…"
                aria-label="Search screens and actions"
                className="flex-1 bg-transparent text-[14.5px] outline-none placeholder:text-fg-subtle"
              />
              <Kbd>Esc</Kbd>
            </div>
            <Command.List className="max-h-[min(380px,54vh)] overflow-y-auto p-1">
              <Command.Empty className="p-6 text-center text-sm text-fg-muted">
                No screen matches that.
              </Command.Empty>
              <Command.Group
                heading="Screens"
                className={groupClass}
              >
                {paletteScreens().map((s) => (
                  <Command.Item
                    key={s.id}
                    value={s.id}
                    keywords={[s.paletteLabel, s.paletteHint, s.navLabel]}
                    onSelect={() => go(screenHref(workspace.slug, s.id))}
                    className={itemClass}
                  >
                    <strong className="text-body font-semibold">{s.paletteLabel}</strong>
                    <span className="text-caption text-fg-subtle">{s.paletteHint}</span>
                  </Command.Item>
                ))}
              </Command.Group>
              <Command.Group
                heading="Actions"
                className={cn(groupClass, "[&_[cmdk-group-heading]]:pt-2.5")}
              >
                <Command.Item
                  value="create-workspace"
                  keywords={["create workspace", "new workspace", "add workspace"]}
                  onSelect={() => {
                    setPaletteOpen(false);
                    router.push("/welcome");
                  }}
                  className={itemClass}
                >
                  <strong className="flex items-center gap-2 text-body font-semibold">
                    <Icon name="plus" size="sm" className="text-fg-subtle" />
                    Create workspace
                  </strong>
                  <span className="text-caption text-fg-subtle">Set up a new workspace for your accounts and team</span>
                </Command.Item>
                {THEME_ACTIONS.map((a) => (
                  <Command.Item
                    key={a.pref}
                    value={`theme-${a.pref}`}
                    keywords={[a.label, "appearance", "mode"]}
                    onSelect={() => {
                      setPreference(a.pref);
                      setPaletteOpen(false);
                    }}
                    className={itemClass}
                  >
                    <strong className="flex items-center gap-2 text-body font-semibold">
                      {a.label}
                      {preference === a.pref ? (
                        <>
                          <Icon name="check" size="sm" className="text-accent" />
                          <span className="sr-only">(current)</span>
                        </>
                      ) : null}
                    </strong>
                  </Command.Item>
                ))}
              </Command.Group>
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
