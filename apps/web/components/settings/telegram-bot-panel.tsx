"use client";

import { channelStatus, saveTelegramBot } from "@mera/sdk";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

export function TelegramBotPanel({ workspaceId, slug, archived }: { workspaceId: string; slug: string; archived: boolean }) {
  const [token, setToken] = useState("");
  const [username, setUsername] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<string>();

  useEffect(() => {
    void settle(channelStatus({ client: browserApi, path: { workspace_id: workspaceId } })).then((result) => {
      if (result.ok) setUsername((result.data.telegram as { bot_username?: string }).bot_username ?? null);
    });
  }, [workspaceId]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || archived) return;
    setBusy(true);
    setFeedback(undefined);
    const result = await settle(saveTelegramBot({
      client: browserApi, path: { workspace_id: workspaceId }, body: { token: token.trim() },
    }));
    if (result.ok) {
      setToken("");
      setUsername((result.data.telegram as { bot_username?: string }).bot_username ?? null);
      setFeedback("Bot saved and webhook registered. Pair your phone in Channels, then send a test message.");
    } else setFeedback(displayMessage(result.error));
    setBusy(false);
  }

  return <Panel><PanelHead title="Telegram approval bot" /><PanelBody className="grid gap-3 text-sm">
    <p className="text-fg-muted">{username ? `Using @${username} for this workspace.` : "Add your own Telegram bot for this workspace."} Workspace admins can save or replace the token here. Members do not need server access.</p>
    <details className="text-fg-muted"><summary className="cursor-pointer font-semibold">How to get a Telegram bot token</summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        <li>Open the verified <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="underline">@BotFather</a> chat in Telegram.</li>
        <li>Send <code>/newbot</code>, then choose a display name and a unique username ending in <code>bot</code>.</li>
        <li>Copy the HTTP API token BotFather sends you. Paste it below and save. Mera validates it, encrypts it, and configures the webhook.</li>
        <li>Open <Link href={`/w/${slug}/channels`} className="underline">Channels</Link>, pair your phone, and send a test message. For troubleshooting, read the <Link href={`/w/${slug}/docs#telegram`} className="underline">Telegram guide</Link>.</li>
      </ol>
    </details>
    {!archived ? <form onSubmit={(event) => void save(event)} className="grid gap-3">
      <Field label="Bot token"><Input type="password" value={token} onChange={(event) => setToken(event.target.value)} required minLength={20} maxLength={256} autoComplete="new-password" disabled={busy} placeholder={username ? "Enter a new token to replace this bot" : "123456789:AA…"} /></Field>
      <Button type="submit" className="justify-self-start" disabled={busy || token.trim().length < 20}>{busy ? "Saving…" : username ? "Replace bot token" : "Save bot token"}</Button>
    </form> : null}
    {feedback ? <Notice role="status">{feedback}</Notice> : null}
  </PanelBody></Panel>;
}
