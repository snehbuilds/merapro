"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import { CreateWorkspaceForm } from "@/components/auth/create-workspace-form";
import { Icon } from "@/components/icons";
import { screenHref } from "@/lib/nav";

export function CreateWorkspaceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();

  function handleSuccess(slug: string) {
    onOpenChange(false);
    router.push(screenHref(slug, "dashboard"));
    router.refresh();
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim animate-in fade-in-0" />
        <Dialog.Content
          aria-labelledby="create-ws-dialog-title"
          aria-describedby="create-ws-dialog-desc"
          className="fixed top-1/2 left-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface p-6 shadow-popover outline-none animate-in fade-in-0 zoom-in-95 max-w-[calc(100vw-2rem)]"
        >
          <div className="flex items-start justify-between gap-4 pb-4">
            <div>
              <Dialog.Title id="create-ws-dialog-title" className="text-lg font-semibold text-fg">
                Create a workspace
              </Dialog.Title>
              <Dialog.Description id="create-ws-dialog-desc" className="mt-1 text-sm text-fg-muted">
                A workspace holds its own accounts, groups, team and approval rules.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                className="cursor-pointer rounded-md p-1.5 text-fg-subtle hover:bg-surface-active hover:text-fg"
                aria-label="Close"
              >
                <Icon name="x" size="sm" />
              </button>
            </Dialog.Close>
          </div>
          <CreateWorkspaceForm
            onSuccess={handleSuccess}
            onCancel={() => onOpenChange(false)}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
