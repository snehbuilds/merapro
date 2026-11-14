"use client";

import { createSource, listServers, testFeed, type SourceType } from "@mera/sdk";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { formErrors } from "@/lib/api/errors";
import type { FeedTest, Source } from "@/lib/api/models";
import {
  DEFAULT_INTERVAL_SECONDS,
  SOURCE_ERROR_TEXT,
  SOURCE_FORM_FIELDS,
  SOURCE_TYPES,
  formatInterval,
  intervalChoices,
  parseKeywords,
  sourceTypeOption,
  type SourceFormField,
} from "@/lib/sources";
import { GroupPicker, type PickerGroup } from "./group-picker";

/*
 * sources.html "Add a source". RSS / Atom, and (P3-M6.6) an approved MCP read tool
 * that needs no approval; the other types are listed, disabled, and say so
 * (docs/12 §4). "Test feed" fetches the URL once without saving and fills an
 * empty Name from the feed's title.
 */

type McpChoice = { serverId: string; server: string; tool: string };

export const ADD_SOURCE_ID = "add-source";

type TestState = { kind: "idle" } | { kind: "testing" } | { kind: "done"; result: FeedTest } | { kind: "error"; message: string };

export function AddSourcePanel({
  workspaceId,
  groups,
  onCreated,
}: {
  workspaceId: string;
  groups: readonly PickerGroup[];
  onCreated: (source: Source) => void;
}) {
  const [type, setType] = useState<SourceType>("rss");
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [interval, setFetchInterval] = useState(DEFAULT_INTERVAL_SECONDS);
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [test, setTest] = useState<TestState>({ kind: "idle" });
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<SourceFormField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [mcpChoices, setMcpChoices] = useState<McpChoice[] | null>(null);
  const [mcpPick, setMcpPick] = useState("");
  const isMcp = type === "mcp";

  useEffect(() => {
    if (!isMcp || mcpChoices !== null) return;
    void settle(listServers({ client: browserApi, path: { workspace_id: workspaceId } })).then((r) => {
      if (!r.ok) return setMcpChoices([]);
      type S = { id: string; name: string; state: string; tools: { name: string; enabled: boolean; risk_class: string; approval_policy: string }[] };
      const servers = (r.data as unknown as { data: S[] }).data;
      setMcpChoices(
        servers
          .filter((x) => x.state === "active")
          .flatMap((x) =>
            x.tools
              .filter((t) => t.enabled && t.risk_class === "read" && t.approval_policy === "auto")
              .map((t) => ({ serverId: x.id, server: x.name, tool: t.name })),
          ),
      );
    });
  }, [isMcp, mcpChoices, workspaceId]);

  function reset() {
    setUrl("");
    setName("");
    setInclude("");
    setExclude("");
    setFetchInterval(DEFAULT_INTERVAL_SECONDS);
    setGroupIds([]);
    setTest({ kind: "idle" });
  }

  async function runTest() {
    setErrors((e) => ({ ...e, url: undefined }));
    if (!url.trim()) {
      setErrors((e) => ({ ...e, url: "Enter the feed's URL first." }));
      return;
    }
    setTest({ kind: "testing" });
    const r = await settle(testFeed({ client: browserApi, path: { workspace_id: workspaceId }, body: { url: url.trim() } }));
    if (!r.ok) {
      const split = formErrors(r.error, ["url"] as const);
      if (split.fields.url) setErrors((e) => ({ ...e, url: split.fields.url }));
      setTest({ kind: "error", message: split.fields.url ?? split.form.map((m) => SOURCE_ERROR_TEXT[r.error.code] ?? m).join(" ") });
      return;
    }
    setTest({ kind: "done", result: r.data });
    if (r.data.ok && r.data.title && !name.trim()) setName(r.data.title);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setFormError(null);
    const local: Partial<Record<SourceFormField, string>> = {};
    const pick = isMcp ? mcpChoices?.find((c) => `${c.serverId}/${c.tool}` === mcpPick) : undefined;
    if (isMcp && !pick) {
      setFormError("Choose a tool.");
      return;
    }
    if (!isMcp && !url.trim()) local.url = "Enter the feed's URL.";
    if (!name.trim()) local.name = "Give the source a name, or use Test feed to take the feed's title.";
    if (Object.keys(local).length > 0) {
      setErrors(local);
      return;
    }
    setPending(true);
    const r = await settle(
      createSource({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspaceId },
        body: {
          source_type: type,
          name: name.trim(),
          url: isMcp ? "" : url.trim(),
          ...(pick ? { mcp_server_id: pick.serverId, mcp_tool: pick.tool } : {}),
          include_keywords: parseKeywords(include),
          exclude_keywords: parseKeywords(exclude),
          fetch_interval_seconds: interval,
          group_ids: groupIds,
        },
      }),
    );
    setPending(false);
    if (!r.ok) {
      const split = formErrors(r.error, SOURCE_FORM_FIELDS);
      setErrors(split.fields);
      if (split.form.length) setFormError(SOURCE_ERROR_TEXT[r.error.code] ?? split.form.join(" "));
      return;
    }
    reset();
    onCreated(r.data);
  }

  const available = SOURCE_TYPES.find((t) => t.type === type)?.available ?? false;
  return (
    <Panel id={ADD_SOURCE_ID} aria-labelledby={`${ADD_SOURCE_ID}-title`} tabIndex={-1} className="outline-none">
      <PanelHead>
        <h2 id={`${ADD_SOURCE_ID}-title`} className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
          Add a source
        </h2>
      </PanelHead>
      <PanelBody>
        <form onSubmit={submit} noValidate aria-label="Add a source" className="grid gap-3.5">
          {formError ? (
            <Notice tone="danger" role="alert">
              {formError}
            </Notice>
          ) : null}
          <Field label="Type" error={errors.source_type} hint="RSS / Atom, or a read tool from an approved MCP server.">
            <Select value={type} onChange={(e) => setType(e.target.value as SourceType)} disabled={pending}>
              {SOURCE_TYPES.map((t) => (
                <option key={t.type} value={t.type} disabled={!t.available}>
                  {sourceTypeOption(t)}
                </option>
              ))}
            </Select>
          </Field>
          {isMcp ? (
            <Field
              label="Tool"
              hint="Only enabled read tools that need no approval can be a source. Set them up under MCP."
            >
              <Select value={mcpPick} disabled={pending} onChange={(e) => setMcpPick(e.target.value)}>
                <option value="">{mcpChoices === null ? "Loading…" : mcpChoices.length ? "Choose a tool" : "No eligible tools"}</option>
                {(mcpChoices ?? []).map((c) => (
                  <option key={`${c.serverId}/${c.tool}`} value={`${c.serverId}/${c.tool}`}>
                    {c.server} · {c.tool}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <Field label="Feed URL" error={errors.url} hidden={isMcp}>
            <Input
              type="url"
              inputMode="url"
              autoComplete="off"
              placeholder="https://example.com/feed.xml"
              value={url}
              disabled={pending}
              onChange={(e) => {
                setUrl(e.target.value);
                setTest({ kind: "idle" });
              }}
            />
          </Field>
          <Field label="Name" error={errors.name} hint="Shown in the source list and on every item it brings in.">
            <Input value={name} maxLength={200} disabled={pending} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-2.5 max-narrow:grid-cols-1">
            <Field label="Include keywords" error={errors.include_keywords} hint="Comma separated. Empty keeps everything.">
              <Input placeholder="postgres, database" value={include} disabled={pending} onChange={(e) => setInclude(e.target.value)} />
            </Field>
            <Field label="Exclude" error={errors.exclude_keywords} hint="Comma separated.">
              <Input placeholder="webinar, sponsored" value={exclude} disabled={pending} onChange={(e) => setExclude(e.target.value)} />
            </Field>
          </div>
          <Field label="Fetch every" error={errors.fetch_interval_seconds}>
            <Select value={interval} disabled={pending} onChange={(e) => setFetchInterval(Number(e.target.value))}>
              {intervalChoices().map((s) => (
                <option key={s} value={s}>
                  {formatInterval(s)}
                </option>
              ))}
            </Select>
          </Field>
          <GroupPicker groups={groups} value={groupIds} onChange={setGroupIds} disabled={pending} error={errors.group_ids} />

          <div aria-live="polite" className="empty:hidden">
            {test.kind === "testing" ? <p className="text-caption text-fg-subtle">Testing the feed…</p> : null}
            {test.kind === "error" ? (
              <Notice tone="danger" title="Test failed.">
                {test.message}
              </Notice>
            ) : null}
            {test.kind === "done" && !test.result.ok ? (
              <Notice tone="danger" title="This URL didn't return a feed.">
                {test.result.error ?? "The feed could not be read."}
              </Notice>
            ) : null}
            {test.kind === "done" && test.result.ok ? <FeedPreview result={test.result} /> : null}
          </div>

          <div className="flex items-center gap-2.5">
            <Button size="sm" onClick={runTest} disabled={pending || test.kind === "testing" || !available || isMcp}>
              {test.kind === "testing" ? "Testing…" : "Test feed"}
            </Button>
            <span className="flex-1" />
            <Button type="submit" size="sm" variant="primary" disabled={pending || !available}>
              {pending ? "Adding…" : "Add source"}
            </Button>
          </div>
        </form>
      </PanelBody>
    </Panel>
  );
}

function FeedPreview({ result }: { result: FeedTest }) {
  const first = result.items.slice(0, 3);
  return (
    <Notice tone="info" icon="check" title={`${result.title ?? "Untitled feed"} · ${result.item_count} ${result.item_count === 1 ? "item" : "items"}.`}>
      {first.length ? (
        <ul className="mt-1.5 grid gap-1">
          {first.map((item) => (
            <li key={item.url} className="truncate text-caption">
              {item.title}
            </li>
          ))}
        </ul>
      ) : (
        "The feed is empty right now; new items arrive on the next fetch."
      )}
    </Notice>
  );
}
