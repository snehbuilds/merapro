"use client";

import { completeConnect, connectChoices, reauthorizeAccount, type AccountOut, type GroupOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { AccountChip } from "@/components/mera/account-chip";
import { DataTable, dataTableColumns } from "@/components/mera/data-table";
import { GroupDot } from "@/components/mera/group-dot";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Time } from "@/components/ui/time";
import {
  ACCOUNT_ERROR_TEXT,
  ALL_ACCOUNTS,
  accountRef,
  accountTypeLabel,
  accountsSummary,
  connectErrorCode,
  connectNotice,
  connectReturnPath,
  filterAccounts,
  isUnfiltered,
  needsAttention,
  platformLabel,
  stripConnectParams,
  initialConnectResult,
  type AccountFilter,
  type ApiPlatform,
  type ConnectResult,
  type ConnectReturn,
} from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { groupSwatch } from "@/lib/groups";
import { SCREENS } from "@/lib/nav";
import { describeAccountHealth } from "@/lib/status";
import { AccountDetail } from "./account-detail";
import { ConnectMenu } from "./connect-menu";
import { PagePicker } from "./page-picker";

/*
 * Connected Accounts (accounts.html): what needs reconnecting, every account
 * with its groups and health, and the selected account's detail. Owners and
 * admins connect, edit and disconnect; everyone else reads.
 */

export interface AccountsScreenProps {
  workspace: { id: string; slug: string; name: string; timezone: string; archived: boolean };
  accounts: AccountOut[];
  groups: GroupOut[];
  /** Owner or admin (CONNECT_ACCOUNTS). */
  canManage: boolean;
  /** Read once from ?connect_code= / ?error= on the server. */
  connectReturn: ConnectReturn | null;
  /** ?account=<id>: preselect this account (dashboard links, duplicate connects). */
  initialAccountId?: string;
}

interface GroupTag {
  id: string;
  name: string;
  swatch: string;
}

interface AccountRow {
  account: AccountOut;
  tz: string;
  groups: GroupTag[];
  selected: boolean;
  onSelect: (id: string) => void;
}

const col = dataTableColumns<AccountRow>();

const columns = [
  col.accessor((r) => r.account.display_name?.trim() || r.account.nickname, {
    id: "account",
    header: "Account",
    cell: ({ row }) => (
      <AccountChip
        size="lg"
        account={accountRef(row.original.account)}
        meta={platformLabel(row.original.account.platform)}
      />
    ),
  }),
  col.accessor((r) => accountTypeLabel(r.account.account_type), {
    id: "type",
    header: "Type",
    enableSorting: false,
    cell: ({ getValue }) => <span className="text-sm text-fg-muted">{getValue()}</span>,
  }),
  col.display({
    id: "groups",
    header: "Groups",
    cell: ({ row }) =>
      row.original.groups.length === 0 ? (
        <span className="text-sm text-fg-subtle">Ungrouped</span>
      ) : (
        <span className="flex flex-wrap items-center gap-x-[7px] gap-y-1">
          {row.original.groups.map((g) => (
            <span key={g.id} className="inline-flex items-center gap-[5px] text-sm">
              <GroupDot group={g.id} style={{ background: g.swatch }} />
              {g.name}
            </span>
          ))}
        </span>
      ),
  }),
  col.accessor(
    (r) => {
      // Sort worst first when descending: danger 2, warn 1, everything else 0.
      const tone = describeAccountHealth(r.account.status, r.account.health).tone;
      return tone === "danger" ? 2 : tone === "warn" ? 1 : 0;
    },
    {
      id: "health",
      header: "Health",
      cell: ({ row }) => {
        const spec = describeAccountHealth(row.original.account.status, row.original.account.health);
        return <StatusPill tone={spec.tone} shape={spec.shape} label={spec.label} data-state={`account:${row.original.account.status}`} />;
      },
    },
  ),
  col.accessor((r) => r.account.token_expires_at ?? "", {
    id: "token",
    header: "Token expires",
    cell: ({ row }) => {
      const a = row.original.account;
      if (a.status === "needs_reauth") return <StatusPill tone="danger" label="Revoked" />;
      return a.token_expires_at ? (
        <Time value={a.token_expires_at} tz={row.original.tz} format="date" />
      ) : (
        <span className="text-sm text-fg-subtle">No expiry</span>
      );
    },
  }),
  col.accessor((r) => r.account.last_health_check_at ?? "", {
    id: "checked",
    header: "Last check",
    cell: ({ row }) => {
      const a = row.original.account;
      return a.last_health_check_at ? (
        <Time value={a.last_health_check_at} tz={row.original.tz} format="datetime" />
      ) : (
        <span className="text-sm text-fg-subtle">Never</span>
      );
    },
  }),
  col.display({
    id: "actions",
    header: () => <span className="sr-only">Actions</span>,
    meta: { align: "end" },
    cell: ({ row }) => (
      <Button
        size="sm"
        variant={row.original.selected ? "default" : "ghost"}
        aria-pressed={row.original.selected}
        aria-label={`Details for ${row.original.account.nickname}`}
        onClick={() => row.original.onSelect(row.original.account.id)}
      >
        Details
      </Button>
    ),
  }),
];

function useReconnect(workspaceId: string, slug: string) {
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  async function reconnect(accountId: string) {
    setPendingId(accountId);
    setError(null);
    const r = await settle(
      reauthorizeAccount({
        client: browserApi,
        path: { workspace_id: workspaceId, account_id: accountId },
        body: { return_path: connectReturnPath(slug) },
      }),
    );
    if (!r.ok) {
      setPendingId(null);
      setError({ id: accountId, message: ACCOUNT_ERROR_TEXT[r.error.code] ?? displayMessage(r.error) });
      return;
    }
    window.location.assign(r.data.authorize_url);
  }
  return { reconnect, pendingId, error };
}

/**
 * Handle the connect redirect: strip ?connect_code= / ?error= from the URL
 * at once (a reload must never replay a code), then redeem the code. Runs
 * once per page load, even under StrictMode's double effects, because a
 * second redemption of the same code would fail.
 */
function useConnectReturn(connectReturn: ConnectReturn | null, onAccount: (id: string) => void) {
  const router = useRouter();
  const [result, setResult] = useState<ConnectResult | null>(() => initialConnectResult(connectReturn));
  const started = useRef(false);
  const onAccountRef = useRef(onAccount);
  useEffect(() => {
    onAccountRef.current = onAccount;
  });

  useEffect(() => {
    if (!connectReturn || started.current) return;
    started.current = true;
    const clean = stripConnectParams(window.location.href);
    if (clean !== window.location.href) window.history.replaceState(null, "", clean);
    if (connectReturn.kind !== "code") return;
    const code = connectReturn.code;
    void settle(completeConnect({ client: browserApi, body: { connect_code: code } })).then(async (r) => {
      if (r.ok) {
        setResult({ kind: r.data.outcome === "reconnected" ? "reconnected" : "connected", accountId: r.data.account_id });
        onAccountRef.current(r.data.account_id);
      } else if (r.error.code === "choice_required") {
        // Several Pages under one sign-in: the code is still good; ask which one.
        const listed = await settle(connectChoices({ client: browserApi, query: { connect_code: code } }));
        setResult(
          listed.ok && listed.data.data.length > 0
            ? { kind: "choose", code, choices: listed.data.data }
            : { kind: "error", code: "unknown", message: listed.ok ? undefined : listed.error.message },
        );
      } else {
        setResult({ kind: "error", code: connectErrorCode(r.error.code), message: r.error.message });
      }
      // Navigate the router itself to the clean URL (which also refreshes server data).
      // A bare router.refresh() re-applies the router's own copy of the URL, which can
      // still carry the spent code if the earlier replaceState ran before hydration.
      const url = new URL(stripConnectParams(window.location.href));
      router.replace(`${url.pathname}${url.search}${url.hash}`, { scroll: false });
    });
  }, [connectReturn, router]);

  return [result, setResult] as const;
}

export function AccountsScreen({ workspace, accounts, groups, canManage, connectReturn, initialAccountId }: AccountsScreenProps) {
  const router = useRouter();
  const canEdit = canManage && !workspace.archived;
  const [filter, setFilter] = useState<AccountFilter>(ALL_ACCOUNTS);
  const [selectedId, setSelectedId] = useState<string | undefined>(() => {
    const fromRedirect = connectReturn?.kind === "error" ? connectReturn.accountId : undefined;
    const wanted = [initialAccountId, fromRedirect].find((id) => id && accounts.some((a) => a.id === id));
    return wanted ?? accounts.find((a) => needsAttention(a))?.id ?? accounts[0]?.id;
  });
  const [connectResult, setConnectResult] = useConnectReturn(connectReturn, setSelectedId);
  const notice = connectResult ? connectNotice(connectResult, accounts) : null;
  const { reconnect, pendingId, error: reconnectError } = useReconnect(workspace.id, workspace.slug);
  const detailHeadingId = useId();

  const groupTags = new Map<string, GroupTag>(groups.map((g) => [g.id, { id: g.id, name: g.name, swatch: groupSwatch(g) }]));
  const attention = accounts.filter((a) => needsAttention(a));
  const reauth = accounts.filter((a) => a.status === "needs_reauth");
  const platforms = [...new Set(accounts.map((a) => a.platform))].sort((a, b) => platformLabel(a).localeCompare(platformLabel(b)));
  const hasUngrouped = accounts.some((a) => a.group_ids.length === 0);
  const visible = filterAccounts(accounts, filter);
  const selected = accounts.find((a) => a.id === selectedId);

  const select = (id: string) => {
    setSelectedId(id);
    // Move focus to the detail heading so keyboard and screen reader users land on what they chose.
    requestAnimationFrame(() => document.getElementById(detailHeadingId)?.focus());
  };

  const rows: AccountRow[] = visible.map((a) => ({
    account: a,
    tz: workspace.timezone,
    groups: a.group_ids.map((id) => groupTags.get(id)).filter((g): g is GroupTag => g !== undefined),
    selected: a.id === selectedId,
    onSelect: select,
  }));

  const togglePlatform = (p: ApiPlatform) => setFilter((f) => ({ ...f, platform: f.platform === p ? undefined : p }));
  const toggleGroup = (g: string) => setFilter((f) => ({ ...f, group: f.group === g ? undefined : g }));

  const disabledReason = !canManage
    ? "Only owners and admins can connect accounts."
    : workspace.archived
      ? "This workspace is archived."
      : undefined;

  return (
    <>
      <Topbar
        title={SCREENS.accounts.title}
        sub={accountsSummary(accounts)}
        actionNode={<ConnectMenu workspaceId={workspace.id} slug={workspace.slug} disabledReason={disabledReason} />}
      />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        {notice ? (
          <Notice
            tone={notice.tone}
            icon={notice.tone === "info" ? "check" : notice.tone === "default" ? "clock" : undefined}
            title={notice.title}
            role={notice.tone === "danger" ? "alert" : "status"}
            actions={
              connectResult?.kind === "pending" ? undefined : (
                <Button size="sm" variant="ghost" onClick={() => setConnectResult(null)}>
                  Dismiss
                </Button>
              )
            }
          >
            {notice.body}
            {connectResult?.kind === "choose" ? (
              <PagePicker
                code={connectResult.code}
                choices={connectResult.choices}
                onDone={(next) => {
                  setConnectResult(next);
                  if (next.kind === "connected" || next.kind === "reconnected") {
                    setSelectedId(next.accountId);
                    router.refresh();
                  }
                }}
              />
            ) : null}
          </Notice>
        ) : null}

        {reauth.map((a) => (
          <Notice
            key={a.id}
            tone="warn"
            title={`${a.nickname} needs reconnecting.`}
            actions={
              canEdit ? (
                <>
                  <Button size="sm" onClick={() => reconnect(a.id)} disabled={pendingId !== null}>
                    {pendingId === a.id ? "Opening…" : "Reconnect"}
                  </Button>
                  {reconnectError?.id === a.id ? (
                    <span role="alert" className="text-caption text-danger">
                      {reconnectError.message}
                    </span>
                  ) : null}
                </>
              ) : undefined
            }
          >
            {a.status_reason ? `${a.status_reason} ` : `${platformLabel(a.platform)} stopped accepting its token. `}
            Its scheduled posts are held; other accounts publish normally.
            {canEdit ? null : " Ask an owner or admin to reconnect it."}
          </Notice>
        ))}

        <Panel>
          <div
            role="toolbar"
            aria-label="Filter accounts"
            className="flex flex-wrap items-center gap-2 border-b border-border px-3.5 py-2.5"
          >
            <Chip pressed={isUnfiltered(filter)} onClick={() => setFilter(ALL_ACCOUNTS)}>
              All
            </Chip>
            <Chip
              pressed={filter.view === "attention"}
              count={attention.length}
              onClick={() => setFilter((f) => ({ ...f, view: f.view === "attention" ? "all" : "attention" }))}
            >
              Needs attention
            </Chip>
            {platforms.length > 1 ? (
              <>
                <span aria-hidden="true" className="h-5 w-px bg-border-strong" />
                {platforms.map((p) => (
                  <Chip key={p} pressed={filter.platform === p} onClick={() => togglePlatform(p)}>
                    {platformLabel(p)}
                  </Chip>
                ))}
              </>
            ) : null}
            {groups.length > 0 ? (
              <>
                <span aria-hidden="true" className="h-5 w-px bg-border-strong" />
                {groups.map((g) => (
                  <Chip key={g.id} pressed={filter.group === g.id} onClick={() => toggleGroup(g.id)}>
                    <GroupDot group={g.id} style={{ background: groupSwatch(g) }} />
                    {g.name}
                  </Chip>
                ))}
                {hasUngrouped ? (
                  <Chip pressed={filter.group === "ungrouped"} onClick={() => toggleGroup("ungrouped")}>
                    Ungrouped
                  </Chip>
                ) : null}
              </>
            ) : null}
            <span className="flex-1" />
            <span className="text-caption text-fg-subtle max-narrow:hidden">
              {canManage ? "Several accounts per platform is the normal case." : "Only owners and admins can change accounts."}
            </span>
          </div>
          <PanelBody flush>
            {accounts.length === 0 ? (
              <EmptyState
                icon="accounts"
                tone="quiet"
                title="No accounts connected"
                description={
                  canManage
                    ? "Connect LinkedIn, Facebook, Instagram, Threads or X with “Connect account”. Several accounts per platform is fine."
                    : "An owner or admin connects the accounts this workspace posts to."
                }
              />
            ) : rows.length === 0 ? (
              <EmptyState
                icon="filter"
                tone="quiet"
                title="No accounts match"
                description="Nothing matches these filters."
                action={
                  <Button size="sm" onClick={() => setFilter(ALL_ACCOUNTS)}>
                    Show all
                  </Button>
                }
              />
            ) : (
              <DataTable
                columns={columns}
                data={rows}
                caption="Connected accounts"
                initialSorting={[{ id: "health", desc: true }]}
                getRowId={(r) => r.account.id}
              />
            )}
          </PanelBody>
          <PanelFoot>
            Connecting an account that is already connected here returns the existing one rather than creating a second:
            two credential sets would race for one account&apos;s rate limits.
          </PanelFoot>
        </Panel>

        {/* While a connect code is being redeemed, nothing else on the page calls the API, so the
            redemption is this page's first request and the URL is already clean when it goes out. */}
        {selected && connectResult?.kind !== "pending" ? (
          <AccountDetail
            key={selected.id}
            account={selected}
            workspaceId={workspace.id}
            tz={workspace.timezone}
            canEdit={canEdit}
            onReconnect={reconnect}
            reconnecting={pendingId === selected.id}
            onDisconnected={() => setSelectedId(undefined)}
            headingId={detailHeadingId}
          />
        ) : null}
      </main>
    </>
  );
}
