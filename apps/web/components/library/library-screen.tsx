"use client";

import {
  createTemplate,
  deleteHashtag,
  deleteTemplate,
  putHashtag,
  updateTemplate,
  type GroupOut,
  type HashtagOut,
  type TemplateOut,
} from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import { composerHref } from "@/lib/composer";
import { SCREENS } from "@/lib/nav";

/*
 * Library (P2-M1.3, P2-M1.4): the workspace hashtag registry and post templates.
 * Banned tags block drafts, block submission, and stop queued posts at publish;
 * preferred tags are offered to the Hashtag agent first. A template fills in the
 * brief, outline, tags and target groups of a new post; each account's post is
 * still written and approved on its own.
 */

type Status = "allowed" | "preferred" | "banned";
const STATUS_LABEL: Record<Status, string> = { preferred: "Preferred", allowed: "Allowed", banned: "Banned" };
const STATUS_TONE = { preferred: "ok", allowed: "neutral", banned: "danger" } as const;

export interface LibraryScreenProps {
  workspace: { id: string; slug: string; archived: boolean };
  hashtags: HashtagOut[];
  templates: TemplateOut[];
  groups: Pick<GroupOut, "id" | "name">[];
  canEdit: boolean;
}

function HashtagPanel({ workspaceId, hashtags, canEdit }: { workspaceId: string; hashtags: HashtagOut[]; canEdit: boolean }) {
  const router = useRouter();
  const [tag, setTag] = useState("");
  const [status, setStatus] = useState<Status>("banned");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function save(t: string, s: Status, key: string) {
    setBusy(key);
    setError(null);
    const r = await settle(putHashtag({ client: browserApi, path: { workspace_id: workspaceId }, body: { tag: t, status: s } }));
    setBusy(null);
    if (!r.ok) {
      setError(r.error.code === "invalid_hashtag" ? "A hashtag is one word, with or without #." : displayMessage(r.error));
      return false;
    }
    router.refresh();
    return true;
  }

  async function onAdd(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (await save(tag, status, "add")) setTag("");
  }

  async function remove(h: HashtagOut) {
    setBusy(h.id);
    const r = await settle(deleteHashtag({ client: browserApi, path: { workspace_id: workspaceId, hashtag_id: h.id } }));
    setBusy(null);
    if (!r.ok) setError(displayMessage(r.error));
    else router.refresh();
  }

  return (
    <Panel aria-labelledby="tags-title">
      <PanelHead title={<span id="tags-title">Hashtag registry</span>} />
      {canEdit ? (
        <PanelBody className="border-b border-border">
          <form className="flex flex-wrap items-end gap-3" onSubmit={onAdd} aria-label="Add a hashtag">
            <Field label="Hashtag" className="min-w-48 flex-1">
              <Input name="tag" value={tag} placeholder="#example" onChange={(e) => setTag(e.target.value)} />
            </Field>
            <Field label="Status">
              <Select name="status" value={status} onChange={(e) => setStatus(e.target.value as Status)}>
                <option value="banned">Banned</option>
                <option value="preferred">Preferred</option>
                <option value="allowed">Allowed</option>
              </Select>
            </Field>
            <Button type="submit" variant="primary" disabled={!tag.trim() || busy !== null}>
              {busy === "add" ? "Saving…" : "Add"}
            </Button>
          </form>
          {error ? (
            <p role="alert" className="mt-2 text-caption text-danger">
              {error}
            </p>
          ) : null}
        </PanelBody>
      ) : null}
      <PanelBody flush>
        {hashtags.length === 0 ? (
          <EmptyState icon="hash" tone="quiet" title="No hashtags in the registry" className="py-6" />
        ) : (
          <ul aria-label="Hashtags">
            {hashtags.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center gap-2 border-b border-border px-3.5 py-2 last:border-b-0">
                <span className="min-w-0 flex-1 truncate font-mono text-sm">#{h.tag}</span>
                {h.aliases.length > 0 ? <span className="text-caption text-fg-subtle">also {h.aliases.map((a) => `#${a}`).join(" ")}</span> : null}
                <Tag tone={STATUS_TONE[h.status as Status] ?? "neutral"}>{STATUS_LABEL[h.status as Status] ?? h.status}</Tag>
                {canEdit ? (
                  <>
                    <Select
                      aria-label={`Status of #${h.tag}`}
                      value={h.status}
                      disabled={busy !== null}
                      onChange={(e) => void save(h.tag, e.target.value as Status, h.id)}
                      className="w-32"
                    >
                      <option value="banned">Banned</option>
                      <option value="preferred">Preferred</option>
                      <option value="allowed">Allowed</option>
                    </Select>
                    <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void remove(h)} aria-label={`Remove #${h.tag}`}>
                      Remove
                    </Button>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </PanelBody>
      <PanelFoot>A banned tag also stops posts already approved and waiting to publish.</PanelFoot>
    </Panel>
  );
}

interface Draft {
  id: string | null;
  name: string;
  topic: string;
  outline: string;
  hashtags: string;
  groupIds: string[];
}

const EMPTY: Draft = { id: null, name: "", topic: "", outline: "", hashtags: "", groupIds: [] };

function TemplateForm({
  workspaceId,
  groups,
  initial,
  onDone,
}: {
  workspaceId: string;
  groups: Pick<GroupOut, "id" | "name">[];
  initial: Draft;
  onDone: () => void;
}) {
  const router = useRouter();
  const [d, setD] = useState<Draft>(initial);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const body = {
      name: d.name.trim(),
      topic: d.topic.trim(),
      outline: d.outline.trim() || null,
      hashtags: d.hashtags.split(/[\s,]+/).filter(Boolean),
      target_group_ids: d.groupIds,
    };
    const r = await settle(
      d.id
        ? updateTemplate({ client: browserApi, path: { workspace_id: workspaceId, template_id: d.id }, body })
        : createTemplate({ client: browserApi, path: { workspace_id: workspaceId }, body }),
    );
    setPending(false);
    if (!r.ok) {
      setError(r.error.code === "template_name_taken" ? "A template with that name already exists." : displayMessage(r.error));
      return;
    }
    router.refresh();
    onDone();
  }

  return (
    <form className="grid gap-3" onSubmit={onSubmit} aria-label={d.id ? "Edit template" : "New template"}>
      {error ? (
        <Notice tone="danger" role="alert">
          {error}
        </Notice>
      ) : null}
      <Field label="Name">
        <Input value={d.name} maxLength={120} required onChange={(e) => setD({ ...d, name: e.target.value })} />
      </Field>
      <Field label="Brief" hint="What the post is about. The Writer drafts each account's post from it.">
        <Textarea value={d.topic} maxLength={2000} required rows={3} onChange={(e) => setD({ ...d, topic: e.target.value })} />
      </Field>
      <Field label="Outline" hint="Optional structure to follow, for example: hook, one number, a link.">
        <Textarea value={d.outline} maxLength={4000} rows={3} onChange={(e) => setD({ ...d, outline: e.target.value })} />
      </Field>
      <Field label="Hashtags" hint="Suggestions for the Writer, separated by spaces.">
        <Input value={d.hashtags} onChange={(e) => setD({ ...d, hashtags: e.target.value })} />
      </Field>
      <fieldset className="grid gap-1.5">
        <legend className="mb-1 text-caption font-medium text-fg">Default groups</legend>
        {groups.length === 0 ? <p className="text-caption text-fg-subtle">No groups yet.</p> : null}
        {groups.map((g) => (
          <label key={g.id} className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={d.groupIds.includes(g.id)}
              onChange={(e) =>
                setD({ ...d, groupIds: e.target.checked ? [...d.groupIds, g.id] : d.groupIds.filter((x) => x !== g.id) })
              }
            />
            {g.name}
          </label>
        ))}
      </fieldset>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={pending || !d.name.trim() || !d.topic.trim()}>
          {pending ? "Saving…" : d.id ? "Save template" : "Create template"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function TemplatePanel({
  workspace,
  templates,
  groups,
  canEdit,
}: {
  workspace: LibraryScreenProps["workspace"];
  templates: TemplateOut[];
  groups: Pick<GroupOut, "id" | "name">[];
  canEdit: boolean;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const names = new Map(groups.map((g) => [g.id, g.name]));

  async function remove(t: TemplateOut) {
    const r = await settle(deleteTemplate({ client: browserApi, path: { workspace_id: workspace.id, template_id: t.id } }));
    if (!r.ok) setError(displayMessage(r.error));
    else router.refresh();
  }

  return (
    <Panel aria-labelledby="templates-title">
      <PanelHead
        title={<span id="templates-title">Post templates</span>}
        actions={
          canEdit && !editing ? (
            <Button size="sm" onClick={() => setEditing(EMPTY)}>
              New template
            </Button>
          ) : undefined
        }
      />
      {editing ? (
        <PanelBody className="border-b border-border">
          <TemplateForm workspaceId={workspace.id} groups={groups} initial={editing} onDone={() => setEditing(null)} />
        </PanelBody>
      ) : null}
      {error ? (
        <PanelBody>
          <p role="alert" className="text-caption text-danger">
            {error}
          </p>
        </PanelBody>
      ) : null}
      <PanelBody flush>
        {templates.length === 0 ? (
          <EmptyState icon="compose" tone="quiet" title="No templates yet" className="py-6" />
        ) : (
          <ul aria-label="Templates">
            {templates.map((t) => (
              <li key={t.id} className="grid gap-1 border-b border-border px-3.5 py-2.5 last:border-b-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{t.name}</span>
                  <Tag tone="outline">used {t.use_count}</Tag>
                  <a className="text-sm font-medium text-accent hover:underline" href={composerHref(workspace.slug, { template: t.id })}>
                    Start a post
                  </a>
                  {canEdit ? (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setEditing({
                            id: t.id,
                            name: t.name,
                            topic: t.topic,
                            outline: t.outline ?? "",
                            hashtags: t.hashtags.map((h) => `#${h}`).join(" "),
                            groupIds: t.target_group_ids,
                          })
                        }
                      >
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void remove(t)} aria-label={`Delete ${t.name}`}>
                        Delete
                      </Button>
                    </>
                  ) : null}
                </div>
                <p className="line-clamp-2 text-caption text-fg-muted">{t.topic}</p>
                {t.target_group_ids.length > 0 ? (
                  <p className="text-caption text-fg-subtle">Targets {t.target_group_ids.map((g) => names.get(g) ?? "a removed group").join(", ")}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </PanelBody>
    </Panel>
  );
}

export function LibraryScreen({ workspace, hashtags, templates, groups, canEdit }: LibraryScreenProps) {
  const editable = canEdit && !workspace.archived;
  return (
    <>
      <Topbar title={SCREENS.library.title} sub={`${hashtags.length} hashtags · ${templates.length} templates`} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <TemplatePanel workspace={workspace} templates={templates} groups={groups} canEdit={editable} />
        <HashtagPanel workspaceId={workspace.id} hashtags={hashtags} canEdit={editable} />
      </main>
    </>
  );
}
