# Runbook: Data requests (access, export and erasure)

How to answer a person who asks for their data or asks to be forgotten, and how to export
a whole workspace. The rules are in [09 - Security](../09-security.md) §9.

## A person asks for a copy of their data

They can do it themselves: **Settings → Your data → Download my data**. It returns
what Mera holds about them as JSON:

- profile, including email and sign-in providers;
- two-step sign-in and approval PIN status;
- workspace memberships and roles;
- paired approval channels;
- their security events (sign-ins, lockouts and the like).

If they cannot sign in, an Owner can export the workspace (below) and extract their rows.

## A person asks to be deleted

1. They press **Settings → Your data → Delete my account** and confirm with their
   password, their authenticator code, or (provider-only accounts) by signing in with
   Google or Microsoft again.
2. They are signed out everywhere at once.
3. After **30 days** the nightly `erase_due` job (04:10 UTC) erases their personal data:
   - name and email are replaced;
   - sessions, provider sign-in links, memberships and approval pairings are deleted;
   - their audit entries keep the action but show a pseudonym.

A **sole Owner** cannot delete their account. They must first make someone else an
Owner, or delete the workspace.

Revoking their tokens at the social platforms is not automatic yet. Disconnect any
accounts they connected under **Accounts** first.

## Someone who commented asks to be forgotten

There is no button for this yet. An Owner, Admin or someone with access to that account
calls `DELETE /api/v1/workspaces/{id}/inbox/comments/{comment_id}/author-data` once per
comment. The author's handle and platform id are removed from that comment, and the
erasure is audited. The comment text stays.

## Export a whole workspace

**Settings → Workspace export → Export everything** (Owners and Admins). A worker builds
a zip of JSON, one file per table. Credentials, tokens and other secret columns are never
included. When the row says **ready**, **Download** gives a link that works for 15
minutes. The export itself is kept for 7 days.

## Deleting a workspace

**Settings → Danger zone → Delete workspace** (Owner). After the grace period the nightly
job removes every row that belongs to it.
