# Runbook: connecting social accounts (Phase 1 platforms)

Phase 1 publishes to LinkedIn (profiles and Pages), Facebook Pages, Instagram (business),
Threads, then X (product decision 2026-11-08). Bluesky and Mastodon are Phase 2.

Each platform needs **one developer app per deployment**. Several accounts on one
platform connect through the same app; each account keeps its own encrypted grant
(`social_account.credentials_ct`, bound to the workspace, purpose and row).

Set the app credentials in `.env`, restart the API and workers, then connect from
**Connected Accounts**. In sandbox mode the fake connector is used whatever is set.

| Platform | Variables | Redirect URI to register |
| --- | --- | --- |
| LinkedIn (profile, Page) | `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | `{MERA_API_URL}/api/v1/social-accounts/oauth/linkedin_member/callback` and `…/linkedin_organization/callback` |
| Facebook Page, Instagram | `META_APP_ID`, `META_APP_SECRET` | `…/oauth/facebook/callback`, `…/oauth/instagram/callback` |
| Threads | `THREADS_APP_ID`, `THREADS_APP_SECRET` | `…/oauth/threads/callback` |
| X | `X_CLIENT_ID`, `X_CLIENT_SECRET` | `…/oauth/x/callback` |

## Scopes Mera asks for

- **LinkedIn profile:** `openid profile w_member_social`.
- **LinkedIn Page:** `w_organization_social r_organization_social rw_organization_admin`
  (Community Management API access must be approved for the app). Mera connects the first
  Page the member administers.
- **Facebook Page:** `pages_show_list pages_manage_posts pages_read_engagement read_insights`.
  Mera stores the **Page** token (non-expiring when derived from a long-lived user token).
- **Instagram:** `instagram_basic instagram_content_publish instagram_manage_insights`
  plus Page access. The Instagram account must be a business account linked to a Page.
  Images are fetched by Instagram from a signed URL, so `MERA_API_URL` must be reachable
  from the internet.
- **Threads:** `threads_basic threads_content_publish threads_manage_insights
  threads_manage_replies`. Long-lived tokens last 60 days and are refreshed by the
  accounts sweep.
- **X:** OAuth 2.0 with PKCE, `tweet.read tweet.write users.read media.write
  offline.access`. The posting quota is **shared by every account on the app's tier**.

## Several Pages under one login

Meta's dialog lets a person pick which Pages to grant. Mera connects the first granted
Page (or the first Page with a linked Instagram account). To connect another Page,
reconnect and grant only that Page in the dialog.

## When an account stops working

The accounts sweep (every five minutes) refreshes tokens at 80% of their lifetime and
checks health every six hours. A revoked or expired grant moves **only that account** to
`needs_reauth`: its posts are held (not failed), a notification and a Telegram alert go
out, and its siblings keep publishing. Reconnect from the account's page; its groups,
history and analytics are kept.
