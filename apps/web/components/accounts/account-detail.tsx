"use client";

import {
  accountCapabilities,
  checkAccountHealth,
  disconnectAccount,
  updateAccount,
  type AccountOut,
  type AccountPatch,
  type PlatformCapabilities,
  type PostingTime,
} from "@mera/sdk";
import { useSecondFactor } from "@/components/mera/second-factor";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { CapabilityTags } from "@/components/mera/capability-tags";
import { Button } from "@/components/ui/button";
import { DescriptionList } from "@/components/ui/description-list";
import { Field, Input } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/ui/status-pill";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { AccountMark } from "@/components/mera/account-chip";
import { ACCOUNT_ERROR_TEXT, accountCapabilityTags, accountTypeLabel, platformIcon, platformLabel } from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage, formErrors, type ApiError } from "@/lib/api/errors";
import { HHMM, formatPostingTimes, parseBoundedInt, readLevelSettings } from "@/lib/level-settings";
import { describeAccountHealth } from "@/lib/status";
import { PostingTimesEditor } from "./posting-times-editor";

/*
 * accounts.html, the two panels under the table for the selected account:
 * who it is and what it can do (capabilities from the adapter descriptor), and
 * its own defaults. Admin+ edits; everyone else reads.
 */

export interface AccountDetailProps {
  account: AccountOut;
  workspaceId: string;
  tz: string;
  /** Owner/admin, and the workspace isn't archived. */
  canEdit: boolean;
  /** Opens the platform to reconnect (reauthorize) this account. */
  onReconnect: (accountId: string) => void;
  reconnecting: boolean;
  onDisconnected: () => void;
  /** The heading id, so the screen can move focus here when a row is chosen. */
  headingId: string;
}

function errorText(error: ApiError): string {
  return ACCOUNT_ERROR_TEXT[error.code] ?? displayMessage(error);
}

function Divider() {
  return <div className="h-px bg-border" />;
}

function SettingRow({ title, hint, children, danger }: { title: ReactNode; hint: ReactNode; children?: ReactNode; danger?: boolean }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <div className={danger ? "text-sm font-semibold text-danger" : "text-sm font-semibold"}>{title}</div>
        <div className="text-xs text-fg-subtle">{hint}</div>
      </div>
      {children}
    </div>
  );
}

/* ---- capabilities ---- */

type CapsState = { kind: "loading" } | { kind: "ok"; caps: PlatformCapabilities } | { kind: "error"; message: string };

function useCapabilities(workspaceId: string, accountId: string): CapsState {
  const [state, setState] = useState<{ id: string; value: CapsState }>({ id: accountId, value: { kind: "loading" } });
  useEffect(() => {
    let live = true;
    settle(accountCapabilities({ client: browserApi, path: { workspace_id: workspaceId, account_id: accountId } })).then((r) => {
      if (!live) return;
      setState({
        id: accountId,
        value: r.ok ? { kind: "ok", caps: r.data } : { kind: "error", message: displayMessage(r.error) },
      });
    });
    return () => {
      live = false;
    };
  }, [workspaceId, accountId]);
  return state.id === accountId ? state.value : { kind: "loading" };
}

function Capabilities({ workspaceId, accountId }: { workspaceId: string; accountId: string }) {
  const caps = useCapabilities(workspaceId, accountId);
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} aria-busy={caps.kind === "loading"}>
      <h3 id={headingId} className="mb-2 text-sm font-semibold">
        What this account can do
      </h3>
      {caps.kind === "loading" ? (
        <div className="flex gap-2">
          <Skeleton className="h-5.5 w-16" />
          <Skeleton className="h-5.5 w-20" />
          <Skeleton className="h-5.5 w-24" />
          <span className="sr-only">Loading capabilities</span>
        </div>
      ) : caps.kind === "error" ? (
        <p className="text-caption text-danger">Couldn&apos;t load capabilities: {caps.message}</p>
      ) : (
        <>
          <CapabilityTags tags={accountCapabilityTags(caps.caps)} />
          <p className="mt-2 text-caption text-fg-subtle">
            Up to <span className="tabular font-mono">{caps.caps.max_chars.toLocaleString("en-US")}</span>{" "}
            {caps.caps.counts_graphemes ? "characters (counted as people see them)" : "characters"}. Read from the
            adapter&apos;s capability descriptor, not hardcoded.
          </p>
          {caps.caps.notes && caps.caps.notes.length > 0 ? (
            <ul className="mt-1.5 grid gap-1 text-caption text-fg-muted">
              {caps.caps.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}
        </>
      )}
    </section>
  );
}

/* ---- nickname ---- */

function NicknameEditor({ account, workspaceId }: { account: AccountOut; workspaceId: string }) {
  const router = useRouter();
  const [value, setValue] = useState(account.nickname);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed = value.trim() !== account.nickname && value.trim() !== "";

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!changed) return;
    setPending(true);
    setError(null);
    const r = await settle(
      updateAccount({
        client: browserApi,
        path: { workspace_id: workspaceId, account_id: account.id },
        body: { nickname: value.trim() },
      }),
    );
    setPending(false);
    if (!r.ok) {
      const split = formErrors(r.error, ["nickname"] as const);
      setError(split.fields.nickname ?? split.form.join(" "));
      return;
    }
    router.refresh();
  }

  return (
    <form onSubmit={save} className="flex items-center justify-end gap-1.5" aria-label="Nickname">
      <Input
        aria-label="Nickname"
        value={value}
        maxLength={80}
        onChange={(e) => setValue(e.target.value)}
        aria-invalid={error ? true : undefined}
        className="min-h-7 max-w-52 py-0.5 text-right text-sm"
      />
      {changed ? (
        <Button type="submit" size="sm" disabled={pending}>
          {pending ? "Saving…" : "Save"}
        </Button>
      ) : null}
      {error ? (
        <span role="alert" className="basis-full text-right text-caption text-danger">
          {error}
        </span>
      ) : null}
    </form>
  );
}

/* ---- defaults ---- */

function samePostingTimes(a: readonly PostingTime[], b: readonly PostingTime[]): boolean {
  return a.length === b.length && a.every((t, i) => t.day === b[i]?.day && t.time === b[i]?.time);
}

function DefaultsForm({ account, workspaceId, tz, canEdit }: { account: AccountOut; workspaceId: string; tz: string; canEdit: boolean }) {
  const router = useRouter();
  const stored = readLevelSettings(account.settings);
  const initialTimes = account.default_posting_times ?? [];
  const [times, setTimes] = useState<PostingTime[]>(initialTimes);
  const [cap, setCap] = useState(stored.daily_post_cap !== undefined && stored.daily_post_cap !== null ? String(stored.daily_post_cap) : "");
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<{ times?: string; cap?: string; form?: string }>({});
  const [saved, setSaved] = useState(false);
  const timesLabel = useId();

  const capValue = parseBoundedInt(cap, 1, 50);
  const capChanged = capValue !== (stored.daily_post_cap ?? null);
  const timesChanged = !samePostingTimes(times, initialTimes);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaved(false);
    const next: typeof errors = {};
    if (capValue === "invalid") next.cap = "Enter a whole number from 1 to 50, or leave it blank to inherit.";
    if (times.some((t) => !HHMM.test(t.time))) next.times = "Each slot needs a time as HH:MM.";
    setErrors(next);
    if (next.cap || next.times) return;

    const body: AccountPatch = {};
    if (timesChanged) {
      const unique = [...new Map(times.map((t) => [`${t.day}-${t.time}`, t])).values()].sort(
        (a, b) => a.day - b.day || a.time.localeCompare(b.time),
      );
      body.default_posting_times = unique.length > 0 ? unique : null;
    }
    if (capChanged) body.settings = { daily_post_cap: capValue as number | null };
    if (Object.keys(body).length === 0) return;

    setPending(true);
    const r = await settle(
      updateAccount({ client: browserApi, path: { workspace_id: workspaceId, account_id: account.id }, body }),
    );
    setPending(false);
    if (!r.ok) {
      const split = formErrors(r.error, ["default_posting_times", "settings.daily_post_cap", "settings"] as const);
      setErrors({
        times: split.fields.default_posting_times,
        cap: split.fields["settings.daily_post_cap"] ?? split.fields.settings,
        form: split.form.length > 0 ? split.form.map((m) => ACCOUNT_ERROR_TEXT[r.error.code] ?? m).join(" ") : undefined,
      });
      return;
    }
    setSaved(true);
    router.refresh();
  }

  if (!canEdit) {
    return (
      <DescriptionList
        items={[
          {
            term: "Posting times",
            detail: initialTimes.length > 0 ? `${formatPostingTimes(initialTimes)} · ${tz}` : "Inherited",
          },
          { term: "Daily post cap", detail: stored.daily_post_cap ? `${stored.daily_post_cap} per day` : "Inherited" },
        ]}
      />
    );
  }

  return (
    <form onSubmit={save} noValidate className="grid gap-4" aria-label="Account defaults">
      {errors.form ? (
        <Notice tone="danger" role="alert">
          {errors.form}
        </Notice>
      ) : null}
      <div className="grid gap-1.5">
        <span id={timesLabel} className="text-sm font-semibold text-fg">
          Posting times <span className="font-normal text-fg-subtle">· {tz}</span>
        </span>
        <PostingTimesEditor value={times} onChange={setTimes} labelledBy={timesLabel} disabled={pending} />
        {errors.times ? (
          <p role="alert" className="text-caption font-medium text-danger">
            {errors.times}
          </p>
        ) : null}
      </div>
      <Field label="Daily post cap" hint="Per account, not per workspace. Leave blank to inherit." error={errors.cap}>
        <Input
          inputMode="numeric"
          value={cap}
          onChange={(e) => setCap(e.target.value)}
          placeholder="Inherit"
          className="max-w-32"
        />
      </Field>
      <div className="flex items-center gap-2.5">
        <Button type="submit" size="sm" disabled={pending || (!timesChanged && !capChanged)}>
          {pending ? "Saving…" : "Save defaults"}
        </Button>
        {saved ? (
          <span role="status" className="text-caption text-fg-subtle">
            Saved.
          </span>
        ) : null}
      </div>
    </form>
  );
}

/* ---- disconnect ---- */

function DisconnectRow({
  account,
  workspaceId,
  onDisconnected,
}: {
  account: AccountOut;
  workspaceId: string;
  onDisconnected: () => void;
}) {
  const router = useRouter();
  const { withSecondFactor } = useSecondFactor();
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const platform = platformLabel(account.platform);

  async function disconnect() {
    setPending(true);
    setError(null);
    const r = await withSecondFactor((headers) =>
      settle(disconnectAccount({ client: browserApi, headers, path: { workspace_id: workspaceId, account_id: account.id } })),
    );
    setPending(false);
    if (!r.ok) {
      setError(errorText(r.error));
      return;
    }
    setConfirming(false);
    onDisconnected();
    router.refresh();
  }

  return (
    <div className="grid gap-2.5">
      <SettingRow danger title="Disconnect this account" hint={`Revokes Mera's access at ${platform} first, then removes it here.`}>
        {confirming ? null : (
          <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
            Disconnect
          </Button>
        )}
      </SettingRow>
      {confirming ? (
        <Notice
          tone="danger"
          title={`Disconnect ${account.nickname}?`}
          actions={
            <>
              <Button size="sm" variant="danger" onClick={disconnect} disabled={pending}>
                {pending ? "Disconnecting…" : `Disconnect ${account.nickname}`}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={pending}>
                Cancel
              </Button>
            </>
          }
        >
          Mera revokes its token at {platform} first, then removes the account from every group. Posts scheduled for it
          won&apos;t publish. Connecting it again later starts fresh.
        </Notice>
      ) : null}
      {error ? (
        <p role="alert" className="text-caption text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/* ---- health ---- */

function HealthActions({
  account,
  workspaceId,
  canEdit,
  onReconnect,
  reconnecting,
}: {
  account: AccountOut;
  workspaceId: string;
  canEdit: boolean;
  onReconnect: (id: string) => void;
  reconnecting: boolean;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  async function check() {
    setPending(true);
    setMessage(null);
    const r = await settle(
      checkAccountHealth({ client: browserApi, path: { workspace_id: workspaceId, account_id: account.id } }),
    );
    setPending(false);
    if (!r.ok) {
      setMessage({ error: true, text: errorText(r.error) });
      return;
    }
    setMessage({ error: false, text: `Checked: ${describeAccountHealth(r.data.status, r.data.health).label}.` });
    router.refresh();
  }

  // Both need CONNECT_ACCOUNTS (owner/admin): a health check spends the account's platform quota.
  if (!canEdit || account.status === "disconnected") return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" onClick={check} disabled={pending}>
        {pending ? "Checking…" : "Check now"}
      </Button>
      <Button
          size="sm"
          variant={account.status === "needs_reauth" ? "primary" : "default"}
          onClick={() => onReconnect(account.id)}
          disabled={reconnecting}
        >
          {reconnecting ? "Opening…" : "Reconnect"}
        </Button>
      {message ? (
        <span role={message.error ? "alert" : "status"} className={message.error ? "text-caption text-danger" : "text-caption text-fg-subtle"}>
          {message.text}
        </span>
      ) : null}
    </div>
  );
}

/* ---- panels ---- */

export function AccountDetail({ account, workspaceId, tz, canEdit, onReconnect, reconnecting, onDisconnected, headingId }: AccountDetailProps) {
  const health = describeAccountHealth(account.status, account.health);
  const lastError = typeof account.health.last_error === "string" ? account.health.last_error : null;
  const empty = <span className="text-fg-subtle">—</span>;

  return (
    <div className="grid grid-cols-2 gap-3.5 max-wide:grid-cols-1">
      <Panel aria-labelledby={headingId}>
        <PanelHead
          title={
            <span id={headingId} tabIndex={-1} className="inline-flex items-center gap-2.5 outline-none">
              <AccountMark platform={platformIcon(account.platform)} avatar_url={account.avatar_url} size="lg" />
              <span>{account.display_name?.trim() || account.nickname}</span>
            </span>
          }
          actions={<StatusPill tone={health.tone} shape={health.shape} label={health.label} />}
        />
        <PanelBody className="grid gap-4">
          {account.status_reason || lastError ? (
            <Notice tone={health.tone === "danger" ? "danger" : "warn"}>{account.status_reason ?? lastError}</Notice>
          ) : null}
          <DescriptionList
            items={[
              { term: "Nickname", detail: canEdit ? <NicknameEditor key={account.nickname} account={account} workspaceId={workspaceId} /> : account.nickname },
              { term: "Platform", detail: platformLabel(account.platform) },
              { term: "Handle", detail: account.handle, mono: true },
              { term: "Platform account", detail: account.platform_account_id, mono: true },
              { term: "Account type", detail: accountTypeLabel(account.account_type) },
              { term: "Connected", detail: account.connected_at ? <Time value={account.connected_at} tz={tz} format="date" /> : empty },
              { term: "Scopes", detail: account.granted_scopes.length > 0 ? account.granted_scopes.join(", ") : empty, mono: true },
              {
                term: "Token expires",
                detail: account.token_expires_at ? <Time value={account.token_expires_at} tz={tz} format="datetime" /> : "No expiry",
              },
              {
                term: "Last health check",
                detail: account.last_health_check_at ? <Time value={account.last_health_check_at} tz={tz} format="datetime" /> : "Never",
              },
            ]}
          />
          <HealthActions
            account={account}
            workspaceId={workspaceId}
            canEdit={canEdit}
            onReconnect={onReconnect}
            reconnecting={reconnecting}
          />
          <Divider />
          <Capabilities workspaceId={workspaceId} accountId={account.id} />
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHead title="Defaults" />
        <PanelBody className="grid gap-4">
          <DefaultsForm key={account.id} account={account} workspaceId={workspaceId} tz={tz} canEdit={canEdit} />
          <Divider />
          <SettingRow title="Autopilot" hint="Assisted and Full Autopilot arrive in Phase 3. Resolves to the strictest of account, group and workspace.">
            <Tag tone="outline">Manual</Tag>
          </SettingRow>
          {canEdit && account.status !== "disconnected" ? (
            <>
              <Divider />
              <DisconnectRow account={account} workspaceId={workspaceId} onDisconnected={onDisconnected} />
            </>
          ) : null}
        </PanelBody>
      </Panel>
    </div>
  );
}
