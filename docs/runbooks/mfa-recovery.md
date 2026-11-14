# Runbook: Two-step sign-in, approval PINs and recovery

Phase 2 adds second factors (P2-M2): an authenticator app (TOTP) for signing in, an
approval PIN for the bots, and single-use grants for high-risk actions.

## For everyone

- **Your security** (user menu) sets up the authenticator, shows **recovery codes once**
  (ten, single use), and sets the approval PIN (6 digits, used in the bot with `/pin`).
- Five wrong PINs lock it for 15 minutes.
- High-risk actions ask for a code or PIN in a dialog: resuming automation, disconnecting
  an account, approving for company pages, removing an Owner, deleting a comment,
  approving an external MCP server, letting an agent write without asking, generating
  video or media above the cost ceiling. A confirmation is good for one action, once,
  for five minutes.

## Lost your phone

1. Sign in with your password, then use a **recovery code** instead of the authenticator
   code. Each code works once.
2. Go to **Your security**, turn the authenticator off, and set it up again on the new
   phone. Generate new recovery codes and store them.

## Lost the phone and the recovery codes

An Owner cannot see or reset your secret. Recovery is an operator action:

1. Verify the person's identity out of band (not by email alone).
2. On the database (owner role), clear their second factor and bump their token version,
   which signs out every session:

```sql
UPDATE "user" SET totp_secret_ct = NULL, mfa_enabled_at = NULL, totp_last_counter = NULL,
       token_version = token_version + 1
 WHERE email = 'person@example.com';
```

3. Record it in the incident log. They sign in with their password and enrol again.

When `MFA_REQUIRED_FOR_ADMINS=true`, an Admin or Owner without a second factor works
as an Editor until they enrol, so a reset never leaves an unprotected Admin.

## Workspace policy

**Settings → Second factors** lists each protected action. Admins can turn a protection
on; turning one off takes an Owner and is audited.
