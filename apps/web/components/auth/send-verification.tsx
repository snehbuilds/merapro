"use client";

import { requestEmailVerification } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Icon } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

/*
 * "Send verification link" (POST /auth/email/verification). The link is
 * emailed; in sandbox the API also returns the verify_url and we show it as a
 * link. Without email delivery it answers 503 email_delivery_unavailable, and
 * 503 email_delivery_failed when the mail server refused the message.
 * `surface="rail"` styles it for the dark rail menu.
 */
type State =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "sent"; verifyUrl: string | null }
  | { kind: "error"; message: string };

export function verificationErrorMessage(code: string, fallback: string): string {
  if (code === "email_delivery_unavailable") return "Email delivery isn't configured on this server, so a verification link can't be sent.";
  if (code === "email_delivery_failed") return "We couldn't send the email just now. Try again in a few minutes.";
  if (code === "already_verified") return "Your email address is already verified.";
  return fallback;
}

export function SendVerification({ surface = "page" }: { surface?: "page" | "rail" }) {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "idle" });

  async function send() {
    setState({ kind: "pending" });
    const result = await settle(requestEmailVerification({ client: browserApi }));
    if (result.ok) {
      setState({ kind: "sent", verifyUrl: result.data.verify_url ?? null });
      return;
    }
    if (result.error.code === "already_verified") router.refresh();
    setState({ kind: "error", message: verificationErrorMessage(result.error.code, displayMessage(result.error)) });
  }

  const rail = surface === "rail";
  const textClass = rail ? "text-2xs text-rail-subtle" : "text-caption text-fg-muted";
  const linkClass = rail ? "font-medium text-rail-hi underline" : "font-medium text-accent underline";

  return (
    <div className="grid gap-1.5">
      {rail ? (
        <button
          type="button"
          role="menuitem"
          onClick={send}
          disabled={state.kind === "pending"}
          className="flex h-7 items-center gap-2.5 rounded-md px-2 text-left text-body text-rail-fg hover:bg-rail-active hover:text-rail-hi focus-visible:bg-rail-active focus-visible:text-rail-hi disabled:opacity-55"
        >
          <Icon name="refresh" size="sm" className="text-rail-subtle" />
          {state.kind === "pending" ? "Sending…" : "Send verification link"}
        </button>
      ) : (
        <Button size="sm" onClick={send} disabled={state.kind === "pending"} className="justify-self-start">
          {state.kind === "pending" ? "Sending…" : state.kind === "sent" ? "Send again" : "Send verification link"}
        </Button>
      )}
      <div aria-live="polite" className={cn(textClass, rail && "px-2")}>
        {state.kind === "sent" ? (
          state.verifyUrl ? (
            <>
              Sandbox: the email went to the capture, not a real inbox.{" "}
              <a href={state.verifyUrl} className={linkClass}>
                Open the verification link
              </a>
            </>
          ) : (
            "Check your inbox for the verification link."
          )
        ) : state.kind === "error" ? (
          <span role="alert" className={rail ? undefined : "text-danger"}>
            {state.message}
          </span>
        ) : null}
      </div>
    </div>
  );
}
