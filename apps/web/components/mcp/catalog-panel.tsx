"use client";

import { installFromCatalog, mcpCatalog } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * The MCP catalog (P3-M6.1): servers Mera knows how to set up, plus the public MCP
 * Registry where it is configured. Installing is registering: the server lands in review
 * with every tool off, exactly like the form below.
 */

type Entry = {
  name: string;
  title: string;
  description: string;
  transport: string;
  url: string | null;
  package: string | null;
  auth_method: string;
  source: string;
  tags: string[];
};

export function CatalogPanel({ workspaceId }: { workspaceId: string }) {
  const id = useId();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [note, setNote] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const path = { workspace_id: workspaceId };

  async function search(query: string) {
    const r = await settle(mcpCatalog({ client: browserApi, path, query: { q: query.trim() || null } }));
    if (r.ok) setEntries((r.data as unknown as { data: Entry[] }).data);
    else setNote({ tone: "danger", text: displayMessage(r.error) });
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loads after mount; state is set after the await
    void search("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  async function install(e: Entry) {
    setBusy(e.name);
    setNote(null);
    const r = await settle(installFromCatalog({ client: browserApi, path: { ...path, name: e.name }, body: {} }));
    setBusy(null);
    if (!r.ok) return setNote({ tone: "danger", text: displayMessage(r.error) });
    setNote({ tone: "info", text: `${e.title} is registered for review. Test the connection, then review its tools.` });
    router.refresh();
  }

  return (
    <Panel aria-labelledby={`${id}-title`}>
      <PanelHead title={<span id={`${id}-title`}>Catalog</span>} />
      <PanelBody className="grid gap-3">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(ev) => {
            ev.preventDefault();
            void search(q);
          }}
        >
          <div className="grid gap-1">
            <Label htmlFor={`${id}-q`}>Search</Label>
            <Input id={`${id}-q`} value={q} placeholder="github, search, notion" onChange={(ev) => setQ(ev.target.value)} />
          </div>
          <Button size="sm" type="submit">
            Search
          </Button>
        </form>
        {note ? (
          <Notice tone={note.tone} role={note.tone === "danger" ? "alert" : "status"}>
            {note.text}
          </Notice>
        ) : null}
        {entries && entries.length === 0 ? <p className="text-sm text-fg-muted">Nothing matches.</p> : null}
        <ul className="grid gap-2 sm:grid-cols-2">
          {(entries ?? []).map((e) => (
            <li key={`${e.source}:${e.name}`} className="grid content-start gap-1 rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{e.title}</span>
                <Tag tone="outline">{e.transport === "http" ? "remote" : "sandboxed"}</Tag>
                {e.auth_method === "oauth" ? <Tag>sign-in</Tag> : e.auth_method !== "none" ? <Tag>needs a key</Tag> : null}
                {e.source === "registry" ? <Tag tone="info">MCP Registry</Tag> : null}
              </div>
              <p className="text-caption text-fg-muted">{e.description}</p>
              <p className="break-all font-mono text-mono-xs text-fg-subtle">{e.url ?? e.package}</p>
              <div>
                <Button size="sm" disabled={busy !== null} onClick={() => void install(e)}>
                  {busy === e.name ? "Registering…" : "Register for review"}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </PanelBody>
    </Panel>
  );
}
