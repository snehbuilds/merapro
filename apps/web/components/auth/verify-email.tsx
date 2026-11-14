"use client";

import { verifyEmail } from "@mera/sdk";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody } from "@/components/ui/panel";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

export function VerifyEmail({ token }: { token: string }) {
  const router = useRouter();
  const [state, setState] = useState<"idle" | "pending" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  async function verify() {
    setState("pending");
    setError(null);
    const result = await settle(verifyEmail({ client: browserApi, body: { token } }));
    if (!result.ok) {
      setState("idle");
      setError(
        result.error.code === "invalid_verification"
          ? "This verification link is invalid or has expired. Send yourself a new one from the account menu."
          : displayMessage(result.error),
      );
      return;
    }
    setState("done");
    // A signed-in session's cached "Unverified" state is stale now.
    router.refresh();
  }

  return (
    <Panel aria-labelledby="verify-title">
      <PanelBody className="grid gap-5 p-6 max-narrow:p-4">
        <div className="grid gap-1">
          <h1 id="verify-title" className="text-lg">
            {state === "done" ? "Email verified" : "Verify your email address"}
          </h1>
          <p className="text-sm text-fg-muted">
            {state === "done"
              ? "Thanks. You can now accept workspace invitations."
              : "Confirm this is your address to finish setting up your account."}
          </p>
        </div>
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        {state === "done" ? (
          <Button asChild variant="primary" className="w-full">
            <Link href="/">Continue to Mera</Link>
          </Button>
        ) : (
          <Button variant="primary" className="w-full" onClick={verify} disabled={state === "pending"}>
            {state === "pending" ? "Verifying…" : "Verify email"}
          </Button>
        )}
      </PanelBody>
    </Panel>
  );
}
