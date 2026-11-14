"use client";

import { createApiKey, revokeApiKey, type ApiKeyOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { DataTable, dataTableColumns } from "@/components/mera/data-table";
import { SecretReveal } from "@/components/mera/secret-reveal";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { displayMessage, formErrors } from "@/lib/api/errors";
import { API_KEY_EXPIRY_OPTIONS, API_KEY_SCOPE_GROUPS, apiKeyState } from "@/lib/api-keys";

/*
 * Settings → API keys (docs/04 §2.4). Admin+ only; the page decides whether
 * to render it. The secret comes back once from POST and is shown once.
 */

export interface ApiKeyRow extends ApiKeyOut {
  tz: string;
  state: "active" | "revoked" | "expired";
  workspaceId: string;
  /** Archived workspace: actions are disabled. */
  readOnly: boolean;
}

const FIELDS = ["name", "scopes", "expires_in_days"] as const;
type KeyField = (typeof FIELDS)[number];

const col = dataTableColumns<ApiKeyRow>();

function when(value: string | null, tz: string, empty: string) {
  return value ? <Time value={value} tz={tz} format="date" /> : <span className="text-sm text-fg-subtle">{empty}</span>;
}

function RevokeButton({ row }: { row: ApiKeyRow }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (row.state !== "active") return null;

  async function revoke() {
    setPending(true);
    setError(null);
    const result = await settle(revokeApiKey({ client: browserApi, path: { workspace_id: row.workspaceId, key_id: row.id } }));
    setPending(false);
    if (!result.ok) {
      setError(displayMessage(result.error));
      return;
    }
    setConfirming(false);
    router.refresh();
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      {confirming ? (
        <>
          <Button size="sm" variant="danger" onClick={revoke} disabled={pending}>
            {pending ? "Revoking…" : `Revoke ${row.name}`}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={pending}>
            Cancel
          </Button>
        </>
      ) : (
        <Button size="sm" onClick={() => setConfirming(true)} disabled={row.readOnly} aria-label={`Revoke ${row.name}`}>
          Revoke
        </Button>
      )}
      {error ? (
        <span role="alert" className="basis-full text-right text-caption text-danger">
          {error}
        </span>
      ) : null}
    </div>
  );
}

const columns = [
    col.accessor("name", {
      header: "Name",
      cell: ({ row }) => (
        <div className="grid gap-0.5">
          <span className="font-medium text-fg">{row.original.name}</span>
          <span className="font-mono text-xs text-fg-subtle">{row.original.key_prefix}…</span>
        </div>
      ),
    }),
    col.accessor((k) => k.scopes.join(" "), {
      id: "scopes",
      header: "Scopes",
      enableSorting: false,
      cell: ({ row }) => (
        <div className="flex max-w-[360px] flex-wrap gap-1">
          {row.original.scopes.map((s) => (
            <Tag key={s} tone={s === "admin" ? "warn" : "outline"}>
              {s}
            </Tag>
          ))}
        </div>
      ),
    }),
    col.accessor("created_at", { header: "Created", cell: ({ row }) => when(row.original.created_at, row.original.tz, "—") }),
    col.accessor((k) => k.last_used_at ?? "", {
      id: "last_used",
      header: "Last used",
      cell: ({ row }) => when(row.original.last_used_at, row.original.tz, "Never"),
    }),
    col.accessor("state", {
      header: "Status",
      cell: ({ row }) => {
        const k = row.original;
        if (k.state === "revoked") return <Tag tone="danger">Revoked</Tag>;
        if (k.state === "expired") return <Tag tone="neutral">Expired</Tag>;
        return k.expires_at ? (
          <span className="grid gap-0.5">
            <Tag tone="ok" className="justify-self-start">
              Active
            </Tag>
            <span className="text-xs text-fg-subtle">
              until <Time value={k.expires_at} tz={k.tz} format="date" />
            </span>
          </span>
        ) : (
          <Tag tone="ok">Active · no expiry</Tag>
        );
      },
    }),
    col.display({
      id: "actions",
      header: () => <span className="sr-only">Actions</span>,
      meta: { align: "end" },
      cell: ({ row }) => <RevokeButton row={row.original} />,
    }),
];

function CreateKeyForm({ workspaceId, onCreated }: { workspaceId: string; onCreated: (secret: string, name: string) => void }) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>([]);
  const [expiry, setExpiry] = useState("90");
  const [pending, setPending] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<KeyField, string>>>({});
  const [formError, setFormError] = useState<string[]>([]);

  function toggle(scope: string) {
    setScopes((cur) => (cur.includes(scope) ? cur.filter((s) => s !== scope) : [...cur, scope]));
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFieldErrors({});
    setFormError([]);
    if (scopes.length === 0) {
      setFieldErrors({ scopes: "Choose at least one scope." });
      return;
    }
    setPending(true);
    const result = await settle(
      createApiKey({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspaceId },
        body: { name: name.trim(), scopes, expires_in_days: expiry === "never" ? null : Number(expiry) },
      }),
    );
    setPending(false);
    if (!result.ok) {
      // invalid_scopes / invalid_expiry may arrive without a field: pin them to theirs.
      const pinned: Partial<Record<string, KeyField>> = { invalid_scopes: "scopes", invalid_expiry: "expires_in_days" };
      const target = pinned[result.error.code];
      if (target && Object.keys(result.error.fieldErrors).length === 0) {
        setFieldErrors({ [target]: result.error.message });
        return;
      }
      const split = formErrors(result.error, FIELDS);
      setFieldErrors(split.fields);
      setFormError(split.form);
      return;
    }
    setName("");
    setScopes([]);
    onCreated(result.data.secret, result.data.key.name);
  }

  const scopeErrorId = "api-key-scopes-error";
  return (
    <form className="grid gap-4" onSubmit={onSubmit} noValidate aria-label="Create API key">
      {formError.length > 0 ? (
        <Notice tone="danger" role="alert">
          {formError.join(" ")}
        </Notice>
      ) : null}
      <div className="grid grid-cols-[minmax(0,1fr)_180px] gap-3 max-narrow:grid-cols-1">
        <Field label="Name" hint="Where it's used, e.g. “n8n ingestion”." error={fieldErrors.name}>
          <Input name="name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Expires" error={fieldErrors.expires_in_days}>
          <Select name="expires_in_days" value={expiry} onChange={(e) => setExpiry(e.target.value)}>
            {API_KEY_EXPIRY_OPTIONS.map((o) => (
              <option key={o.label} value={o.days === null ? "never" : String(o.days)}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <fieldset className="grid gap-2" aria-describedby={fieldErrors.scopes ? scopeErrorId : undefined}>
        <legend className="mb-1.5 text-sm font-semibold text-fg">Scopes</legend>
        {API_KEY_SCOPE_GROUPS.map((group) => (
          <div key={group.label} className="flex flex-wrap items-center gap-1.5">
            <span className="w-36 shrink-0 text-caption text-fg-muted max-narrow:w-full">{group.label}</span>
            {group.scopes.map((scope) => (
              <Chip key={scope} pressed={scopes.includes(scope)} onClick={() => toggle(scope)} className="font-mono">
                {scope}
              </Chip>
            ))}
          </div>
        ))}
        {scopes.includes("admin") ? (
          <Notice tone="warn">
            <strong>admin</strong> lets this key manage members, keys and settings. Prefer narrower scopes.
          </Notice>
        ) : null}
        {fieldErrors.scopes ? (
          <p id={scopeErrorId} className="text-caption font-medium text-danger">
            {fieldErrors.scopes}
          </p>
        ) : null}
      </fieldset>
      <div>
        <Button type="submit" variant="primary" disabled={pending || !name.trim()}>
          {pending ? "Creating key…" : "Create key"}
        </Button>
      </div>
    </form>
  );
}

export function ApiKeysPanel({
  workspaceId,
  keys,
  tz,
  archived,
}: {
  workspaceId: string;
  keys: ApiKeyOut[];
  tz: string;
  archived: boolean;
}) {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [revealed, setRevealed] = useState<{ secret: string; name: string } | null>(null);
  const rows: ApiKeyRow[] = keys.map((k) => ({ ...k, tz, state: apiKeyState(k), workspaceId, readOnly: archived }));

  return (
    <Panel aria-labelledby="api-keys-title">
      <PanelHead
        title={<span id="api-keys-title">API keys</span>}
        actions={
          creating ? null : (
            <Button size="sm" onClick={() => setCreating(true)} disabled={archived}>
              New key
            </Button>
          )
        }
      />
      {revealed ? (
        <div className="border-b border-border p-3.5">
          <SecretReveal
            title={`Key “${revealed.name}” created.`}
            value={revealed.secret}
            label="API key secret"
            onDismiss={() => setRevealed(null)}
          >
            Copy the secret now and store it somewhere safe.
          </SecretReveal>
        </div>
      ) : null}
      {creating ? (
        <div className="border-b border-border p-3.5">
          <CreateKeyForm
            workspaceId={workspaceId}
            onCreated={(secret, name) => {
              setCreating(false);
              setRevealed({ secret, name });
              router.refresh();
            }}
          />
          <Button size="sm" variant="ghost" className="mt-2" onClick={() => setCreating(false)}>
            Cancel
          </Button>
        </div>
      ) : null}
      <PanelBody flush>
        {rows.length === 0 ? (
          <EmptyState
            icon="link"
            tone="quiet"
            title="No API keys"
            description="Keys let scripts, n8n and MCP clients act in this workspace with the scopes you choose."
          />
        ) : (
          <DataTable
            columns={columns}
            data={rows}
            caption="API keys"
            initialSorting={[{ id: "created_at", desc: true }]}
            getRowId={(k) => k.id}
          />
        )}
      </PanelBody>
      <PanelFoot>
        A key acts with its scopes and never more than its creator&apos;s role. Revoking is immediate and cannot be undone.
      </PanelFoot>
    </Panel>
  );
}
