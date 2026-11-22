/* Shared loading skeleton for workspace pages. Next.js shows this instantly
   while the server component fetches data, eliminating the blank screen on
   tab switches. */
export default function Loading() {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div
        className="size-6 animate-spin rounded-full border-2 border-fg-muted border-t-transparent"
        role="status"
        aria-label="Loading"
      />
    </div>
  );
}
