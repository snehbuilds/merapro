import { Panel } from "@/components/ui/panel";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <>
      <p role="status" className="sr-only">Loading workspace page</p>
      <div aria-hidden="true" className="flex h-(--topbar-h) shrink-0 items-center gap-3 border-b border-border bg-surface px-6 max-rail:px-3">
        <Skeleton className="h-3 w-24 max-rail:hidden" />
        <Skeleton className="h-4 w-32" />
        <Skeleton className="ml-auto h-8 w-45 max-rail:w-8" />
        <Skeleton className="size-8" />
      </div>
      <main id="main" tabIndex={-1} aria-busy="true" className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <Panel aria-hidden="true">
          <div className="flex items-center justify-between gap-3 border-b border-border px-3.5 py-3">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-6 w-16" />
          </div>
          <SkeletonRows rows={5} />
        </Panel>
      </main>
    </>
  );
}
