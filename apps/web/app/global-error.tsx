"use client";

import { useEffect } from "react";
import "./globals.css";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // The browser console retains the stack and Next.js digest for support without silently
    // transmitting potentially sensitive client state to a third party.
    console.error("web.unhandled_render_error", {
      message: "An unhandled client rendering error occurred.",
      errorType: error.name,
      errorMessage: error.message,
      digest: error.digest,
      stack: error.stack,
    });
  }, [error]);

  return (
    <html lang="en">
      <body className="bg-bg text-fg">
        <main className="mx-auto flex min-h-screen max-w-xl items-center px-6 py-12">
          <section className="w-full rounded-md border border-border bg-bg-raised p-6 shadow-sm">
            <p className="font-label text-sm font-medium uppercase tracking-wide text-fg-subtle">Mera</p>
            <h1 className="mt-2 text-2xl font-semibold">Something went wrong</h1>
            <p className="mt-2 text-fg-muted">Try again. If this continues, contact support with the time it happened.</p>
            <button className="mt-6 rounded-sm bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover focus:outline-none focus:ring-2 focus:ring-focus" onClick={reset} type="button">
              Try again
            </button>
          </section>
        </main>
      </body>
    </html>
  );
}
