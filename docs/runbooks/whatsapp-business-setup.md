# Runbook: WhatsApp approvals

WhatsApp is a second approval channel next to Telegram (P2-M4, docs/07 §4). It runs on
the same bot runtime: the same cards, decisions, `/pin`, edit-by-reply and commands. It
is **off until configured**: with no WhatsApp settings, the Approval Channels screen says
"Not set up" and nothing is sent.

One WhatsApp number serves every workspace on a deployment. A person pairs a chat once,
and that pairing covers every workspace they belong to.

## 1. What you need from Meta

1. A Meta Business account (business verification is required before you can message
   people beyond the test numbers).
2. A Meta app with the **WhatsApp** product added.
3. A phone number registered to the WhatsApp Business Platform, not in use in the
   WhatsApp app. Note its **Phone number ID** (WhatsApp → API Setup).
4. A **permanent access token**: Business Settings → Users → System users → add a system
   user with the `whatsapp_business_messaging` permission on the app, then generate a
   token. The temporary token on the API Setup page expires in a day; do not use it.
5. The app's **App secret** (App settings → Basic). Mera checks every webhook against it.

## 2. Three message templates

Outside the 24-hour customer-service window, WhatsApp only delivers pre-approved
templates. Mera uses three. Create them under WhatsApp Manager → Message templates, in the
language you set as `WHATSAPP_TEMPLATE_LANGUAGE` (default `en`), category **Utility**:

| Name | Body | Button |
| --- | --- | --- |
| `mera_approval_waiting` | `Approval waiting in Mera: {{1}}` | Quick reply: `Show me` |
| `mera_update` | `Update from Mera: {{1}}` | Quick reply: `Show me` |
| `mera_daily_digest` | `Your Mera digest: {{1}}` | Quick reply: `Show me` |

`{{1}}` is the first lines of the card or message, flattened to one line (Meta refuses
newlines in variables). Mera sends the quick reply's payload as `o`. Tapping **Show me**
opens the window, and Mera then sends every open card for that chat in full.

`mera_daily_digest` carries the daily approval digest (Phase 3) for people who chose a
digest in Settings. Without it, digests still reach Telegram and email.

Wait for the templates to be approved before turning WhatsApp on. A template that is
not approved makes the send fail, and the card stays available in the web queue.

## 3. Configure Mera

Set these in `.env` (never commit them). The first four go together: setting some but not
all of them refuses to start.

| Variable | Value |
| --- | --- |
| `WHATSAPP_PHONE_NUMBER_ID` | the Phone number ID from API Setup |
| `WHATSAPP_ACCESS_TOKEN` | the system user's permanent token |
| `WHATSAPP_APP_SECRET` | the app secret; verifies `X-Hub-Signature-256` |
| `WHATSAPP_VERIFY_TOKEN` | 16+ random characters you choose: `openssl rand -hex 24` |
| `WHATSAPP_DISPLAY_NUMBER` | optional: the number as people dial it, digits only (e.g. `4915112345678`), for one-tap `wa.me` pairing links |
| `WHATSAPP_TEMPLATE_LANGUAGE` | optional: the templates' language code, default `en` |
| `WHATSAPP_MONTHLY_TEMPLATE_CAP` | optional: templates per calendar month (UTC), default `1000` |

Restart `api`, `bots` and `worker` after changing them.

## 4. Register the webhook

The reverse proxy must send `/api/v1/webhooks/` to the `bots` service (port 8001) before
the general `/api` rule, exactly as for Telegram.

In the Meta app: WhatsApp → Configuration → Webhook → Edit.

- Callback URL: `https://YOUR-HOST/api/v1/webhooks/whatsapp`
- Verify token: the value of `WHATSAPP_VERIFY_TOKEN`

Meta calls `GET` with `hub.mode=subscribe`; `bots` echoes `hub.challenge` only for the
right token (403 otherwise). Then subscribe the **messages** field.

Every `POST` is checked against `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with
the app secret) in constant time, before the body is parsed. A bad or missing signature
gets 401 and nothing else happens. A good one is queued as `approvals:whatsapp_update`
and answered 200 at once; delivery statuses are acknowledged and dropped.

## 5. Pair and test

1. In Mera, open **Approval channels**, pick **WhatsApp** under "Pair your phone", and get
   a code.
2. Send `/pair MERA-XXXX-XXXX` to the business number (or use the one-tap link if
   `WHATSAPP_DISPLAY_NUMBER` is set). The code works once, for ten minutes, and only on
   WhatsApp: a Telegram code does not pair a WhatsApp chat.
3. **Send test message** on the WhatsApp panel. It should arrive at once, since you just
   messaged Mera.
4. Admins can turn WhatsApp cards off per workspace with the panel's switch.

## 6. How it behaves

- **Buttons.** Up to three become reply buttons; more become one list ("Choose", up to
  ten rows). Link buttons become links in the text.
- **No edits.** WhatsApp cannot change a sent message. When a card reaches a final state
  (approved, rejected, expired), Mera sends a short follow-up instead. A tap on an old card
  is always re-checked on the server, so a stale button cannot approve anything.
- **The window.** Within 24 hours of the person's last message to Mera, cards arrive in
  full. Outside it, Mera sends a template, which Meta bills.
- **The budget.** Each template counts toward `WHATSAPP_MONTHLY_TEMPLATE_CAP`. When it is
  spent, templates are refused until the next month; the web approval queue keeps
  working. The panel shows "Templates this month: n of cap".

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Meta says the callback could not be verified | `WHATSAPP_VERIFY_TOKEN` matches the value in Meta, and `/api/v1/webhooks/` reaches `bots` |
| `bots` logs 401 for every message | `WHATSAPP_APP_SECRET` is the secret of the same app the webhook belongs to |
| "WhatsApp refused the message (131047)" | Outside the window and the template is missing or not approved |
| "the monthly WhatsApp template budget is spent" | Raise the cap, or wait for the month to roll over |
| Pairing says the code is invalid | The code was minted for Telegram, expired, or was already used |
| Nothing arrives, no errors | The workspace's WhatsApp switch is off, or the person is marked away |

## 8. Rotating credentials

- **Access token:** generate a new system-user token, update `WHATSAPP_ACCESS_TOKEN`,
  restart, then revoke the old token in Business Settings.
- **App secret:** reset it in the Meta app and update `WHATSAPP_APP_SECRET` straight
  away; webhooks are refused (401) until both sides match.
- **Unpairing:** a person unpairs from the Approval Channels screen. Every outstanding
  button issued to them stops working, in every workspace.
