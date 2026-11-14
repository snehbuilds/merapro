# Runbook: Telegram approval bot

One bot serves every workspace on a deployment (docs/07 §6). A person pairs a chat once,
and that pairing covers every workspace they belong to.

## 1. Create the bot

1. In Telegram, message `@BotFather`, send `/newbot`, and choose a name and a username
   (it must end in `bot`).
2. Keep the token BotFather returns. Treat it like a password.
3. Optional: `/setcommands` with the list from `/help` (queue, today, stats, groups,
   accounts, workspaces, switch, pause, kill, resume, whoami).

## 2. Configure Mera

Set these in `.env` (never commit them):

| Variable | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | the BotFather token |
| `TELEGRAM_BOT_USERNAME` | the bot's username, without `@` (used for one-tap pairing links) |
| `TELEGRAM_WEBHOOK_SECRET` | 16+ random characters: `openssl rand -hex 24` |
| `TELEGRAM_MODE` | `webhook` (servers) or `polling` (local development only) |

The API refuses to start with a token and no secret, and production refuses `polling`.

## 3a. Production: webhook

The reverse proxy must send `/api/v1/webhooks/` to the `bots` service (port 8001)
**before** the general `/api` rule (docs/12 §1.2 P6). Then register the webhook once:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d url="https://YOUR-HOST/api/v1/webhooks/telegram" \
  -d secret_token="$TELEGRAM_WEBHOOK_SECRET" \
  -d 'allowed_updates=["message","callback_query"]'
```

`bots` checks `X-Telegram-Bot-Api-Secret-Token` in constant time (401 on a mismatch, and
nothing else happens), queues the update, and answers 200 at once. The `approvals` worker
does the work. Duplicate deliveries are processed once (by `update_id`).

## 3b. Local development: long polling

No public HTTPS is needed:

```bash
cd backend && uv run python -m mera.bots.poll
```

It removes any webhook and feeds updates into the same `approvals:telegram_update` job.
Keep a worker running with the `approvals` queue.

## 4. Pair your phone

1. Web app → **Approval Channels** → **Get a pairing code**.
2. Tap the one-tap link, or send `/pair MERA-XXXX-XXXX` to the bot within ten minutes.
3. The bot answers "Paired with Mera as you@example.com (Acme, Northwind)".
4. **Send test message** confirms the round trip.

Codes are single use and stored hashed. Five wrong codes from one chat lock that chat for
an hour. Unpairing (Approval Channels → Unpair) kills every outstanding button issued to
you, in every workspace.

## 5. Without a bot (sandbox)

With no token in sandbox mode, cards go to the **sandbox phone**:
`/w/<workspace>/sandbox`. Pair it the same way (`/pair CODE` typed into the phone), then
tap its buttons. Everything runs through the real bot runtime.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No cards arrive | The person is paired (Approval Channels → Paired approvers), may approve at least one account on the post, and Telegram cards are on for the workspace. A worker runs the `approvals` queue. |
| `getWebhookInfo` shows 401s | `TELEGRAM_WEBHOOK_SECRET` differs between Mera and `setWebhook`. Re-run `setWebhook`. |
| Taps say "This button belongs to someone else" | The card was forwarded or the chat was re-paired to another person: working as designed (logged as `security.identity_mismatch`). |
| Taps say "This post changed" | Someone edited an account after the card was sent. The card refreshes; use its new buttons. |
| "That code is not valid" | Codes expire in ten minutes and work once. Generate a new one. |
