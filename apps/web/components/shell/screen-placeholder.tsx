import { notFound } from "next/navigation";
import { Icon } from "@/components/icons";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { SCREENS, isScreenEnabled, type ScreenId } from "@/lib/nav";
import { Topbar } from "./topbar";

/** F1 placeholder: the real topbar for the screen, and an empty panel. */
export function ScreenPlaceholder({ id }: { id: ScreenId }) {
  if (!isScreenEnabled(id)) notFound();
  const screen = SCREENS[id];
  return (
    <>
      <Topbar title={screen.title} sub={screen.sub} action={screen.action} />
      <main id="main" tabIndex={-1} className="flex-1 p-6 outline-none max-rail:p-4">
        <Panel>
          <PanelHead title={screen.title} />
          <PanelBody>
            <div className="grid place-items-center gap-2 px-6 py-11 text-center">
              <Icon name={screen.icon} size="xl" className="text-fg-subtle" />
              <p className="max-w-[46ch] text-sm text-fg-muted">Not built yet — see docs/12 §4</p>
            </div>
          </PanelBody>
        </Panel>
      </main>
    </>
  );
}
