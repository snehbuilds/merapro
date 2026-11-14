/** First focusable element on every page; jumps past the rail and topbar. */
export function SkipLink() {
  return (
    <a
      href="#main"
      data-slot="skip-link"
      className="fixed top-2 left-2 z-300 -translate-y-[200%] rounded-md bg-accent px-3 py-2 text-sm font-semibold text-accent-fg focus:translate-y-0"
    >
      Skip to content
    </a>
  );
}
