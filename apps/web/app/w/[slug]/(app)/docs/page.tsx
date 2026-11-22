import type { Metadata } from "next";
import Link from "next/link";
import { Topbar } from "@/components/shell/topbar";
import { SCREENS, type ScreenId } from "@/lib/nav";

export const metadata: Metadata = { title: "Help & docs" };

const guides: { id: ScreenId; steps: string[] }[] = [
  { id: "dashboard", steps: ["Review queued posts, upcoming publishing, account health and recent agent activity.", "Use New post to open Composer. Follow alerts to the relevant queue or account."] },
  { id: "approvals", steps: ["Open a card to review each account's version, media, checks and requested time.", "Approve, reject with a reason, request changes or reschedule. A decision affects the selected account version; verify the target before submitting.", "Use the pending filters and approval history to track work and tool calls awaiting permission."] },
  { id: "composer", steps: ["Select the social accounts or groups that should receive the post.", "Write the draft, attach media, inspect each account preview, then save, request approval or schedule.", "Edit account-specific versions when wording or limits differ by platform."] },
  { id: "calendar", steps: ["Change week to inspect scheduled posts by day and group.", "Open an item to review its status and timing. Use New post to fill an open slot."] },
  { id: "inbox", steps: ["Filter comments and replies by account and attention state.", "Open a conversation, draft a response, and approve or reject suggested replies before they are sent."] },
  { id: "analytics", steps: ["Choose a date range and account or group filter.", "Compare reach, engagement and publishing results; inspect individual posts to understand changes."] },
  { id: "sources", steps: ["Add a feed or source URL, test it, and assign it to groups.", "Pause, edit or remove a source when its content is irrelevant or its fetch fails."] },
  { id: "discovery", steps: ["Choose a group to see scored stories and trending topics.", "Open a candidate to inspect its source and relevance, then use it to start a draft."] },
  { id: "voice", steps: ["Create a writing profile with tone, audience, examples and rules.", "Assign profiles to groups or accounts, then update them as you review drafts."] },
  { id: "library", steps: ["Manage saved templates, hashtags and reusable content.", "Open an item to edit it or use it when composing a post."] },
  { id: "accounts", steps: ["Select a platform and connect its account through the provider sign-in flow.", "If you use your own platform app, first enter its client ID and secret in Workspace settings. Copy the callback URL shown there into the platform's developer console.", "Use Test or Reconnect when a token expires; disconnect accounts that should no longer publish."] },
  { id: "groups", steps: ["Create groups for a brand, topic or campaign and add social accounts.", "Set group-level voice and automation rules, then review the assigned accounts before publishing."] },
  { id: "agents", steps: ["Inspect agent runs, drafts, decisions and errors.", "Open a run to see what it did; use its available controls to retry or stop it."] },
  { id: "automation", steps: ["Set the workspace's autopilot mode and review any group or account overrides.", "Use the kill switch to stop automated actions immediately; inspect the current mode before re-enabling."] },
  { id: "channels", steps: ["Enable approval cards for the workspace.", "Save a Telegram bot token below, pair your phone, then send a test message.", "Review paired approvers and revoke a chat that should no longer receive cards. WhatsApp controls appear when that channel is configured."] },
  { id: "mcp", steps: ["Create a client credential and copy it when shown; it cannot be recovered later.", "Configure the Mera MCP server URL and credential in your assistant, then test tool discovery.", "Review tool permissions and approval requests before allowing external actions; revoke unused clients."] },
  { id: "team", steps: ["Invite members and assign a role appropriate to their work.", "Review memberships and account access, then remove access when someone leaves."] },
  { id: "audit", steps: ["Filter events by action, actor or date.", "Open an event to inspect the recorded change and its source."] },
  { id: "settings", steps: ["Update workspace name, time zone and other workspace defaults.", "In social app credentials, choose the platform, copy its client ID and secret from its developer console, and save them here. Use the callback URL displayed in Mera exactly.", "Create API keys for integrations, configure security settings, and review the danger zone before archiving a workspace."] },
];

export default async function DocsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <>
    <Topbar title="Help & docs" sub="Workspace guide" />
    <main id="main" tabIndex={-1} className="flex-1 space-y-8 overflow-y-auto p-6 outline-none max-rail:p-4">
      <div className="max-w-4xl space-y-3">
        <p className="text-sm text-fg-muted">Choose a menu below for its everyday tasks. Admin setup guides follow.</p>
        <nav aria-label="Guide sections" className="flex flex-wrap gap-2">{guides.map(({ id }) =>
          <a key={id} href={`#${id}`} className="rounded-md border border-border px-2 py-1 text-sm hover:bg-surface">{SCREENS[id].navLabel}</a>)}</nav>
      </div>
      <div className="grid max-w-5xl gap-4 lg:grid-cols-2">{guides.map(({ id, steps }) =>
        <section id={id} key={id} className="scroll-mt-6 rounded-lg border border-border bg-surface p-5">
          <h2 className="text-lg font-semibold">{SCREENS[id].navLabel}</h2>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-fg-muted">{steps.map((step) => <li key={step}>{step}</li>)}</ol>
          <Link href={`/w/${slug}/${id}`} className="mt-4 inline-block text-sm font-medium underline">Open {SCREENS[id].navLabel}</Link>
        </section>)}</div>
      <section id="telegram" className="max-w-5xl scroll-mt-6 rounded-lg border border-border bg-surface p-5">
        <h2 className="text-lg font-semibold">Telegram approval bot: full setup</h2>
        <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-fg-muted">
          <li>In Telegram, open <a className="underline" href="https://t.me/BotFather" target="_blank" rel="noreferrer">@BotFather</a>. Confirm the account is the verified Telegram bot.</li>
          <li>Send <code>/newbot</code>. Enter a display name and a unique username ending in <code>bot</code>.</li>
          <li>Copy the HTTP API token BotFather returns. Treat it like a password. If it is exposed, use BotFather&apos;s <code>/revoke</code> command and save the new token in Mera.</li>
          <li>As a workspace admin, open <Link className="underline" href={`/w/${slug}/channels`}>Channels</Link>, paste the token in Telegram → Bot token, and select Save bot token. Mera validates the token and registers its webhook automatically.</li>
          <li>Turn Telegram cards on. Under Pair your phone, create a single-use code and open the bot link. Press Start or send the displayed <code>/pair</code> command. Codes expire after ten minutes.</li>
          <li>Return to Channels and select Send test message. If nothing arrives, check that you paired this bot, the channel is enabled, and the bot token is current. Replace the token in the same form if needed.</li>
        </ol>
        <p className="mt-3 text-sm text-fg-muted">Each workspace can save its own bot. Workspace members need no access to deployment environment variables. Tokens are stored encrypted and are never displayed after saving.</p>
      </section>
      <section id="platform-keys" className="max-w-5xl scroll-mt-6 rounded-lg border border-border bg-surface p-5">
        <h2 className="text-lg font-semibold">Social platform client keys</h2>
        <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-fg-muted">
          <li>Open <Link className="underline" href={`/w/${slug}/settings`}>Workspace settings</Link> → Social app credentials and choose the platform. Copy the callback URL shown by Mera.</li>
          <li>In that platform&apos;s official developer console, create an app for the appropriate product, enable its sign-in or publishing permissions, and add the Mera callback URL exactly.</li>
          <li>Copy its client ID (sometimes called App ID or Key) and client secret (sometimes App Secret). Paste both in Mera and save. Keep the secret private.</li>
          <li>Open Accounts, connect a social account, complete the provider consent screen, and verify the connection in Mera. If approval or review is required by the platform, complete that in its console.</li>
        </ol>
      </section>
    </main>
  </>;
}
