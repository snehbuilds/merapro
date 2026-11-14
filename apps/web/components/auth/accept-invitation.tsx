"use client";

import { acceptInvitation, listWorkspaces } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { SendVerification } from "./send-verification";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot } from "@/components/ui/panel";
import { browserApi, sessionLogout, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { screenHref } from "@/lib/nav";
import { loginHref } from "@/lib/session/next-path";

const ACCEPT_ERRORS: Readonly<Record<string, string>> = {
  invalid_invitation: "This invitation link is invalid or has expired.",
  already_member: "You're already a member of this workspace.",
  workspace_archived: "This workspace is archived and isn't taking new members.",
};

export function AcceptInvitation({ token, userName, userEmail }: { token: string; userName: string; userEmail: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);

  async function switchAccount() {
    await sessionLogout();
    router.replace(loginHref(`/invite/${encodeURIComponent(token)}`));
    router.refresh();
  }

  async function accept() {
    setPending(true);
    setError(null);
    const accepted = await settle(acceptInvitation({ client: browserApi, body: { token } }));
    if (!accepted.ok) {
      setPending(false);
      if (accepted.error.code === "email_unverified") {
        setNeedsVerification(true);
        return;
      }
      setError(ACCEPT_ERRORS[accepted.error.code] ?? displayMessage(accepted.error));
      return;
    }
    // The accept response names the workspace by id; routes use its slug.
    const list = await settle(listWorkspaces({ client: browserApi }));
    const ws = list.ok ? list.data.data.find((w) => w.id === accepted.data.workspace_id) : undefined;
    router.replace(ws ? screenHref(ws.slug, "dashboard") : "/");
    router.refresh();
  }

  return (
    <Panel aria-labelledby="invite-title">
      <PanelBody className="grid gap-5 p-6 max-narrow:p-4">
        <div className="grid gap-1">
          <h1 id="invite-title" className="text-lg">
            Join a workspace
          </h1>
          <p className="text-sm text-fg-muted">
            You&apos;ve been invited to a Mera workspace. You&apos;ll join as <strong className="text-fg">{userName}</strong>{" "}
            <span className="font-mono text-xs">({userEmail})</span>.
          </p>
        </div>
        {error ? (
          <Notice tone="danger" role="alert">
            {error}
          </Notice>
        ) : null}
        {needsVerification ? (
          <Notice tone="warn" title="Verify your email address first." role="alert">
            Invitations can only be accepted from a verified address. Open the link we send you, then come back to this page.
            <div className="mt-2.5">
              <SendVerification />
            </div>
          </Notice>
        ) : null}
        <Button variant="primary" className="w-full" onClick={accept} disabled={pending}>
          {pending ? "Joining…" : "Accept invitation"}
        </Button>
      </PanelBody>
      <PanelFoot>
        Not you?{" "}
        <button type="button" className="font-medium text-accent hover:underline" onClick={switchAccount}>
          Sign in with another account
        </button>
      </PanelFoot>
    </Panel>
  );
}
