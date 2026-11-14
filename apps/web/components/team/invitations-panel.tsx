"use client";

import { createInvitation, revokeInvitation, type InvitationOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { SecretReveal } from "@/components/mera/secret-reveal";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { displayMessage, formErrors } from "@/lib/api/errors";
import { INVITE_FORM_ID, INVITE_ROLES, roleLabel, type InviteRole } from "@/lib/workspace";

/*
 * Team → invitations (Admin+). The invitation is emailed when the server has
 * email delivery configured; the accept link also comes back once from POST and
 * is shown once, so the inviter can pass it on if the email goes astray.
 */

const FIELDS = ["email", "role"] as const;
type InviteField = (typeof FIELDS)[number];

const INVITE_ERRORS: Readonly<Record<string, string>> = {
  already_member: "That person is already a member of this workspace.",
  invitation_pending: "There's already a pending invitation for that email. Revoke it to send a new one.",
  workspace_archived: "This workspace is archived, so nobody new can join.",
  forbidden: "You don't have permission to invite people here.",
};

function InviteForm({ workspaceId, disabled, onInvited }: { workspaceId: string; disabled: boolean; onInvited: (email: string, url: string, emailed: boolean) => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<InviteRole>("editor");
  const [pending, setPending] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<InviteField, string>>>({});
  const [formError, setFormError] = useState<string[]>([]);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setFieldErrors({});
    setFormError([]);
    const result = await settle(
      createInvitation({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspaceId },
        body: { email: email.trim(), role },
      }),
    );
    setPending(false);
    if (!result.ok) {
      const known = INVITE_ERRORS[result.error.code];
      if (known) {
        const aboutEmail = result.error.code === "already_member" || result.error.code === "invitation_pending";
        if (aboutEmail) setFieldErrors({ email: known });
        else setFormError([known]);
        return;
      }
      const split = formErrors(result.error, FIELDS);
      setFieldErrors(split.fields);
      setFormError(split.form);
      return;
    }
    setEmail("");
    onInvited(result.data.invitation.email, result.data.accept_url, result.data.email_sent ?? false);
  }

  return (
    <form id={INVITE_FORM_ID} className="grid gap-3" onSubmit={onSubmit} noValidate aria-label="Invite someone">
      {formError.length > 0 ? (
        <Notice tone="danger" role="alert">
          {formError.join(" ")}
        </Notice>
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)_160px_auto] items-start gap-3 max-narrow:grid-cols-1">
        <Field label="Email" error={fieldErrors.email}>
          <Input
            name="email"
            type="email"
            autoComplete="off"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={disabled}
          />
        </Field>
        <Field label="Role" error={fieldErrors.role}>
          <Select name="role" value={role} onChange={(e) => setRole(e.target.value as InviteRole)} disabled={disabled}>
            {INVITE_ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit" variant="primary" disabled={disabled || pending || !email.trim()} className="mt-[26px] max-narrow:mt-0">
          {pending ? "Inviting…" : "Invite"}
        </Button>
      </div>
    </form>
  );
}

function InvitationItem({ invitation, workspaceId, tz, disabled }: { invitation: InvitationOut; workspaceId: string; tz: string; disabled: boolean }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function revoke() {
    setPending(true);
    setError(null);
    const result = await settle(
      revokeInvitation({ client: browserApi, path: { workspace_id: workspaceId, invitation_id: invitation.id } }),
    );
    setPending(false);
    if (!result.ok) {
      setError(INVITE_ERRORS[result.error.code] ?? displayMessage(result.error));
      return;
    }
    router.refresh();
  }

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3.5 py-2.5 last:border-b-0">
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{invitation.email}</span>
      <Tag tone="outline">{roleLabel(invitation.role)}</Tag>
      <span className="text-xs text-fg-subtle">
        expires <Time value={invitation.expires_at} tz={tz} format="datetime" />
      </span>
      <Button size="sm" variant="ghost" onClick={revoke} disabled={disabled || pending} aria-label={`Revoke invitation for ${invitation.email}`}>
        {pending ? "Revoking…" : "Revoke"}
      </Button>
      {error ? (
        <span role="alert" className="basis-full text-caption text-danger">
          {error}
        </span>
      ) : null}
    </li>
  );
}

export function InvitationsPanel({
  workspaceId,
  invitations,
  tz,
  archived,
}: {
  workspaceId: string;
  invitations: InvitationOut[];
  tz: string;
  archived: boolean;
}) {
  const router = useRouter();
  const [revealed, setRevealed] = useState<{ email: string; url: string; emailed: boolean } | null>(null);

  return (
    <Panel aria-labelledby="invitations-title">
      <PanelHead title={<span id="invitations-title">Invitations</span>} />
      <PanelBody className="grid gap-3.5 border-b border-border">
        <InviteForm
          workspaceId={workspaceId}
          disabled={archived}
          onInvited={(email, url, emailed) => {
            setRevealed({ email, url, emailed });
            router.refresh();
          }}
        />
        {revealed ? (
          <SecretReveal
            title={`Invitation for ${revealed.email} created.`}
            value={revealed.url}
            label="invitation link"
            onDismiss={() => setRevealed(null)}
          >
            {revealed.emailed
              ? "We emailed them this link. It's shown once here in case the email doesn't arrive."
              : "No email was sent from this server: share this link with them yourself."}
          </SecretReveal>
        ) : null}
      </PanelBody>
      <PanelBody flush>
        {invitations.length === 0 ? (
          <EmptyState icon="team" tone="quiet" title="No pending invitations" className="py-6" />
        ) : (
          <ul aria-label="Pending invitations">
            {invitations.map((inv) => (
              <InvitationItem key={inv.id} invitation={inv} workspaceId={workspaceId} tz={tz} disabled={archived} />
            ))}
          </ul>
        )}
      </PanelBody>
      <PanelFoot>An invitation link works once, for the email it was sent to, until it expires.</PanelFoot>
    </Panel>
  );
}
