"use client";

import {
  mintPairingCode,
  revokeBinding,
  saveTelegramBot,
  setTelegram,
  setWhatsapp,
  testTelegram,
  testWhatsapp,
  type BindingOut,
  type ChannelStatus,
  type PairingCodeOut,
} from "@mera/sdk";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { DescriptionList } from "@/components/ui/description-list";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { SCREENS } from "@/lib/nav";

/*
 * Approval Channels (approval-channels.html, M7; WhatsApp P2-M4). Pairing binds a chat to
 * *you* across every workspace you belong to (docs/07 §6): one code, one tap, one channel
 * per code. Without a bot token the sandbox phone stands in for Telegram, so the whole loop
 * can be tried locally. WhatsApp appears once the deployment has its Cloud API settings;
 * outside the 24-hour window it sends a paid template, so the month's count is shown.
 */

type Channel = "telegram" | "whatsapp";
const CHANNEL_NAME: Record<string, string> = { telegram: "Telegram", whatsapp: "WhatsApp" };

interface WhatsAppStatus {
  configured: boolean;
  display_number: string | null;
  enabled: boolean;
  last_test_at: string | null;
  last_test_result: string | null;
  usage: { template: number; session: number; cap: number } | null;
}

export interface ChannelsScreenProps {
  workspace: { id: string; slug: string; timezone: string; archived: boolean };
  status: ChannelStatus;
  bindings: BindingOut[];
  canManage: boolean;
}

export function ChannelsScreen({ workspace, status, bindings, canManage }: ChannelsScreenProps) {
  const router = useRouter();
  const [code, setCode] = useState<PairingCodeOut | null>(null);
  const [flash, setFlash] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [botToken, setBotToken] = useState("");
  const tg = status.telegram as {
    configured: boolean;
    sandbox: boolean;
    bot_username: string | null;
    mode: string;
    enabled: boolean;
    last_test_at: string | null;
    last_test_result: string | null;
  };
  const wa = status.whatsapp as unknown as WhatsAppStatus;
  const [pairOn, setPairOn] = useState<Channel>("telegram");
  const mine = bindings.filter((b) => b.is_me);
  const mineOn = (c: Channel) => mine.filter((b) => b.channel_type === c).length;
  const path = { workspace_id: workspace.id };

  async function run<T>(work: () => Promise<{ ok: true; data: T } | { ok: false; error: Parameters<typeof displayMessage>[0] }>, ok: (d: T) => string) {
    setBusy(true);
    setFlash(null);
    const r = await work();
    setBusy(false);
    if (!r.ok) {
      setFlash({ tone: "danger", text: displayMessage(r.error) });
      return null;
    }
    setFlash({ tone: "info", text: ok(r.data) });
    router.refresh();
    return r.data;
  }

  return (
    <>
      <Topbar title={SCREENS.channels.title} sub={tg.configured ? `Telegram · @${tg.bot_username ?? "bot"}` : "Telegram · sandbox"} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div role="status" aria-live="polite" className="empty:hidden">
          {flash?.tone === "info" ? <Notice tone="info" icon="check">{flash.text}</Notice> : null}
        </div>
        {flash?.tone === "danger" ? <Notice tone="danger" role="alert">{flash.text}</Notice> : null}

        <div className="grid gap-3.5 lg:grid-cols-2">
          <Panel>
            <PanelHead
              title="Telegram"
              actions={
                <Switch
                  aria-label="Send approval cards to Telegram for this workspace"
                  checked={tg.enabled}
                  disabled={!canManage || busy || workspace.archived}
                  onCheckedChange={(enabled) =>
                    void run(() => settle(setTelegram({ client: browserApi, path, body: { enabled } })), () =>
                      enabled ? "Telegram cards are on for this workspace." : "Telegram cards are off for this workspace.",
                    )
                  }
                />
              }
            />
            <PanelBody className="grid gap-3">
              {tg.sandbox ? (
                <Notice tone="info" title="Sandbox phone">
                  No bot token is configured, so cards go to the sandbox phone instead of Telegram.{" "}
                  <Link className="font-semibold underline" href={`/w/${workspace.slug}/sandbox`}>
                    Open the sandbox inspector
                  </Link>
                  .
                </Notice>
              ) : null}
              {canManage ? (
                <div className="grid gap-2 text-sm text-fg-muted">
                  <p className="font-semibold text-fg">{tg.configured ? "Replace Telegram bot" : "Set up the Telegram bot"}</p>
                  <ol className="list-decimal space-y-1 pl-5">
                    <li>Open <a className="underline" href="https://t.me/BotFather" target="_blank" rel="noreferrer">@BotFather</a> in Telegram, send <code>/newbot</code>, and follow the prompts.</li>
                    <li>Copy the HTTP API token BotFather gives you and paste it below. Mera verifies it with Telegram and stores it encrypted for this workspace.</li>
                    <li>Save, pair your chat below, and send a test message. You can replace the token here if it changes.</li>
                  </ol>
                  <label htmlFor="telegram-token" className="font-medium text-fg">Bot token</label>
                  <input id="telegram-token" type="password" autoComplete="off" value={botToken}
                    onChange={(event) => setBotToken(event.target.value)} disabled={busy || workspace.archived}
                    placeholder={tg.configured ? "Enter a new token to replace the current bot" : "123456789:AA…"}
                    className="w-full rounded-md border border-border bg-surface px-3 py-2 text-fg" />
                  <Button size="sm" disabled={busy || workspace.archived || botToken.trim().length < 20}
                    onClick={() => void run(() => settle(saveTelegramBot({ client: browserApi, path, body: { token: botToken.trim() } })),
                      () => { setBotToken(""); return "Telegram bot saved. Pair your chat and send a test message."; })}>
                    Save bot token
                  </Button>
                  <p>The token is never shown again. Only workspace admins can replace it. <Link className="underline" href={`/w/${workspace.slug}/docs#telegram`}>Detailed setup guide</Link></p>
                </div>
              ) : null}
              <DescriptionList
                items={[
                  { term: "Bot", detail: tg.bot_username ? `@${tg.bot_username}` : "Sandbox" },
                  { term: "Delivery", detail: tg.mode === "polling" ? "Long polling (local development)" : "Webhook" },
                  { term: "Cards", detail: tg.enabled ? "On for this workspace" : "Off for this workspace" },
                  {
                    term: "Last test",
                    detail: tg.last_test_at ? (
                      <>
                        <Time value={tg.last_test_at} tz={workspace.timezone} format="datetime" /> · {tg.last_test_result}
                      </>
                    ) : (
                      "Never"
                    ),
                  },
                ]}
              />
            </PanelBody>
            <PanelFoot>
              <Button
                size="sm"
                disabled={busy || mineOn("telegram") === 0}
                onClick={() =>
                  void run(() => settle(testTelegram({ client: browserApi, path })), (d) =>
                    d.error ? `The test did not arrive: ${d.error}` : `Test sent to ${d.sent} chat${d.sent === 1 ? "" : "s"}.`,
                  )
                }
              >
                Send test message
              </Button>
            </PanelFoot>
          </Panel>

          <Panel>
            <PanelHead title="Pair your phone" />
            <PanelBody className="grid gap-3">
              <p className="text-sm text-fg-muted">
                One pairing serves every workspace you belong to. Cards always name their workspace, and only you can use
                the buttons on yours.
              </p>
              {wa.configured ? (
                <fieldset className="flex flex-wrap gap-4 text-sm">
                  <legend className="sr-only">Channel to pair</legend>
                  {(["telegram", "whatsapp"] as const).map((c) => (
                    <label key={c} className="flex items-center gap-1.5">
                      <input
                        type="radio"
                        name="pair-channel"
                        value={c}
                        checked={pairOn === c}
                        onChange={() => {
                          setPairOn(c);
                          setCode(null);
                        }}
                      />
                      {CHANNEL_NAME[c]}
                    </label>
                  ))}
                </fieldset>
              ) : null}
              {code ? (
                <div className="grid gap-2 rounded-md border border-border p-3">
                  <p className="font-mono text-lg tracking-wider" aria-label="Pairing code">
                    {code.code}
                  </p>
                  <p className="text-sm">
                    Send <code className="font-mono">{code.command}</code> to{" "}
                    {pairOn === "whatsapp" ? "Mera's WhatsApp number" : "the bot"}
                    {code.deep_link ? (
                      <>
                        , or{" "}
                        <a className="font-semibold text-accent underline" href={code.deep_link} target="_blank" rel="noreferrer">
                          open {CHANNEL_NAME[pairOn]} and pair in one tap
                        </a>
                      </>
                    ) : null}
                    .
                  </p>
                  <p className="text-caption text-fg-subtle">
                    Single use · expires <Time value={code.expires_at} tz={workspace.timezone} format="time" />
                  </p>
                </div>
              ) : null}
            </PanelBody>
            <PanelFoot>
              <Button
                size="sm"
                variant="primary"
                disabled={busy}
                onClick={async () => {
                  const d = await run(() => settle(mintPairingCode({ client: browserApi, path, body: { channel_type: pairOn } })), () => "Code ready. It works once, for ten minutes.");
                  if (d) setCode(d);
                }}
              >
                {code ? "New code" : "Get a pairing code"}
              </Button>
            </PanelFoot>
          </Panel>
        </div>

        <Panel>
          <PanelHead title="Paired approvers" />
          {bindings.length === 0 ? (
            <PanelBody>
              <EmptyState icon="channels" title="Nobody has paired a chat yet" description="Cards go only to paired people who can approve the accounts on them." />
            </PanelBody>
          ) : (
            <ul className="divide-y divide-border">
              {bindings.map((b) => (
                <li key={b.id} className="flex flex-wrap items-center gap-2.5 px-3.5 py-2.5 text-sm">
                  <span className="font-medium">{b.user_name}</span>
                  {b.is_me ? <Tag tone="info">You</Tag> : null}
                  <span className="text-fg-muted">
                    {CHANNEL_NAME[b.channel_type] ?? b.channel_type}
                    {b.external_label ? ` · ${b.external_label}` : ""}
                  </span>
                  <span className="flex-1" />
                  <span className="text-caption text-fg-subtle">
                    paired <Time value={b.verified_at} tz={workspace.timezone} format="date" />
                  </span>
                  {b.is_me ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      onClick={() =>
                        void run(() => settle(revokeBinding({ client: browserApi, path: { ...path, binding_id: b.id } })), () =>
                          "Unpaired. Every button sent to that chat stopped working.",
                        )
                      }
                    >
                      Unpair
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel>
          <PanelHead
            title="WhatsApp"
            actions={
              wa.configured ? (
                <Switch
                  aria-label="Send approval cards to WhatsApp for this workspace"
                  checked={wa.enabled}
                  disabled={!canManage || busy || workspace.archived}
                  onCheckedChange={(enabled) =>
                    void run(() => settle(setWhatsapp({ client: browserApi, path, body: { enabled } })), () =>
                      enabled ? "WhatsApp cards are on for this workspace." : "WhatsApp cards are off for this workspace.",
                    )
                  }
                />
              ) : (
                <Tag>Not set up</Tag>
              )
            }
          />
          <PanelBody className="grid gap-3">
            {wa.configured ? (
              <>
                <DescriptionList
                  items={[
                    { term: "Number", detail: wa.display_number ? `+${wa.display_number}` : "Set" },
                    { term: "Cards", detail: wa.enabled ? "On for this workspace" : "Off for this workspace" },
                    {
                      term: "Templates this month",
                      detail: wa.usage
                        ? `${wa.usage.template} of ${wa.usage.cap} · ${wa.usage.session} in-window messages`
                        : "None",
                    },
                    {
                      term: "Last test",
                      detail: wa.last_test_at ? (
                        <>
                          <Time value={wa.last_test_at} tz={workspace.timezone} format="datetime" /> · {wa.last_test_result}
                        </>
                      ) : (
                        "Never"
                      ),
                    },
                  ]}
                />
                <p className="text-caption text-fg-subtle">
                  Within 24 hours of your last message to Mera, cards arrive in full. After that, Mera sends a short
                  template with a Show me button, which counts toward the month&apos;s template budget.
                </p>
              </>
            ) : (
              <p className="text-sm text-fg-muted">
                WhatsApp needs a Meta business account, a verified number and two approved message templates. An operator
                sets it up once for the deployment; see the WhatsApp Business setup runbook.
              </p>
            )}
          </PanelBody>
          {wa.configured ? (
            <PanelFoot>
              <Button
                size="sm"
                disabled={busy || mineOn("whatsapp") === 0}
                onClick={() =>
                  void run(() => settle(testWhatsapp({ client: browserApi, path })), (d) =>
                    d.error ? `The test did not arrive: ${d.error}` : `Test sent to ${d.sent} chat${d.sent === 1 ? "" : "s"}.`,
                  )
                }
              >
                Send test message
              </Button>
            </PanelFoot>
          ) : null}
        </Panel>
      </main>
    </>
  );
}
