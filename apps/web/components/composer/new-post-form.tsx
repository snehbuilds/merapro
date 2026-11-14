"use client";

import { createPostSet, type AccountOut, type GroupOut, type TemplateOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";
import { isWebUrl } from "@/components/discovery/trending-card";
import { Icon } from "@/components/icons";
import { AccountStack } from "@/components/mera/account-chip";
import { GroupDot } from "@/components/mera/group-dot";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Tag } from "@/components/ui/tag";
import { platformIcon, platformLabel } from "@/lib/accounts";
import { browserApi, idempotencyHeaders, settle } from "@/lib/api/browser";
import { formErrors } from "@/lib/api/errors";
import type { ContentItem } from "@/lib/api/models";
import {
  MAX_TARGETS,
  NEW_POST_FIELDS,
  POST_SET_ERROR_TEXT,
  composerHref,
  overlapNote,
  predictTargets,
  rememberSkipped,
} from "@/lib/composer";
import { groupSwatch } from "@/lib/groups";

/*
 * composer.html with no set yet: the target picker (groups expand into
 * accounts, deduplicated server-side) and the subject, either a content
 * item from Discovery (?item=<id>) or a topic brief. Create queues drafting
 * and opens the set (?set=<id>).
 */

type NewPostField = (typeof NEW_POST_FIELDS)[number];

export interface NewPostFormProps {
  workspace: { id: string; slug: string };
  groups: GroupOut[];
  accounts: AccountOut[];
  /** Prefilled from ?item=<id>. */
  item: ContentItem | null;
  /** Editor+ and the workspace isn't archived. */
  canEdit: boolean;
  /** Post templates (P2-M1.4); the outline and hashtags are added by the API. */
  templates?: TemplateOut[];
  /** Prefilled from ?template=<id>. */
  initialTemplateId?: string;
}

export function NewPostForm({ workspace, groups, accounts, item, canEdit, templates = [], initialTemplateId }: NewPostFormProps) {
  const router = useRouter();
  const startTemplate = templates.find((t) => t.id === initialTemplateId) ?? null;
  const [templateId, setTemplateId] = useState<string | null>(startTemplate?.id ?? null);
  const [groupIds, setGroupIds] = useState<string[]>(startTemplate ? startTemplate.target_group_ids.filter((g) => groups.some((x) => x.id === g)) : []);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [showAccounts, setShowAccounts] = useState(false);
  const [useItem, setUseItem] = useState(item !== null);
  const [topic, setTopic] = useState(startTemplate?.topic ?? "");
  const [title, setTitle] = useState(startTemplate?.name ?? "");

  function chooseTemplate(id: string) {
    const t = templates.find((x) => x.id === id) ?? null;
    setTemplateId(t?.id ?? null);
    if (!t) return;
    setTopic(t.topic);
    setTitle(t.name);
    setGroupIds(t.target_group_ids.filter((g) => groups.some((x) => x.id === g)));
  }
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<NewPostField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const id = useId();
  const targetsLabelId = `${id}-targets`;
  const targetsErrorId = `${id}-targets-error`;
  const accountsId = `${id}-accounts`;

  const live = [...accounts].filter((a) => a.status !== "disconnected").sort((a, b) => a.nickname.localeCompare(b.nickname));
  const reached = predictTargets(groupIds, accountIds, groups, accounts);
  const reachedAccounts = reached.map((rid) => accounts.find((a) => a.id === rid)).filter((a): a is AccountOut => !!a);
  const overlap = overlapNote(groupIds, groups);
  const inChosenGroup = new Set(groups.filter((g) => groupIds.includes(g.id)).flatMap((g) => g.members.map((m) => m.social_account_id)));
  const disabled = !canEdit || pending;

  const toggle = (list: string[], value: string) => (list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setFormError(null);
    const local: Partial<Record<NewPostField, string>> = {};
    if (groupIds.length === 0 && accountIds.length === 0) local.targets = "Pick at least one group or account.";
    else if (reached.length === 0) local.targets = "None of those accounts is connected and active.";
    else if (reached.length > MAX_TARGETS) local.targets = `At most ${MAX_TARGETS} accounts per post; this reaches ${reached.length}.`;
    if (!(useItem && item) && !topic.trim()) local.topic_brief = "Start from a content item or write a topic brief.";
    if (Object.keys(local).length) {
      setErrors(local);
      return;
    }
    setPending(true);
    const r = await settle(
      createPostSet({
        client: browserApi,
        headers: idempotencyHeaders(),
        path: { workspace_id: workspace.id },
        body: {
          ...(useItem && item ? { content_item_id: item.id } : { topic: topic.trim() }),
          title: title.trim() || null,
          targets: { account_group_ids: groupIds, social_account_ids: accountIds },
          template_id: templateId,
          generate: true,
        },
      }),
    );
    if (!r.ok) {
      setPending(false);
      const split = formErrors(r.error, NEW_POST_FIELDS);
      const fields = { ...split.fields };
      // The API puts no_targets / too_many_targets / unknown_group on "targets": show the specific copy there.
      if (fields.targets && POST_SET_ERROR_TEXT[r.error.code]) fields.targets = POST_SET_ERROR_TEXT[r.error.code];
      setErrors(fields);
      if (split.form.length) setFormError(POST_SET_ERROR_TEXT[r.error.code] ?? split.form.join(" "));
      return;
    }
    rememberSkipped(r.data.id, r.data.skipped ?? []);
    router.push(composerHref(workspace.slug, { set: r.data.id }));
  }

  return (
    <form onSubmit={submit} noValidate aria-label="New post" className="grid gap-3.5">
      {formError ? (
        <Notice tone="danger" role="alert">
          {formError}
        </Notice>
      ) : null}

      {templates.length > 0 ? (
        <Field label="Start from a template" hint="Fills in the brief, title and groups. Its outline and hashtags guide the Writer.">
          <Select value={templateId ?? ""} disabled={disabled} onChange={(e) => chooseTemplate(e.target.value)}>
            <option value="">No template</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        </Field>
      ) : null}

      <Panel>
        <div
          role="group"
          aria-labelledby={targetsLabelId}
          aria-describedby={errors.targets ? targetsErrorId : undefined}
          className="flex flex-wrap items-center gap-2 rounded-t-lg border-b border-border px-3.5 py-2.5"
        >
          <span id={targetsLabelId} className="text-sm font-semibold whitespace-nowrap">
            Targets
          </span>
          {groups.map((g) => (
            <Chip
              key={g.id}
              pressed={groupIds.includes(g.id)}
              count={g.member_count}
              disabled={disabled}
              onClick={() => setGroupIds((l) => toggle(l, g.id))}
            >
              <GroupDot group={g.id} style={{ background: groupSwatch(g) }} />
              {g.name}
            </Chip>
          ))}
          {groups.length > 0 ? <span aria-hidden="true" className="h-5 w-px bg-border-strong" /> : null}
          <Chip
            variant="filter"
            className="[&>[data-slot=icon]:first-child]:hidden"
            aria-expanded={showAccounts}
            aria-controls={accountsId}
            disabled={disabled}
            onClick={() => setShowAccounts((s) => !s)}
          >
            <Icon name="plus" size="sm" />
            Add an account
            {accountIds.length > 0 ? <span className="tabular font-mono text-2xs">{accountIds.length}</span> : null}
          </Chip>
          <span className="flex-1" />
          {overlap ? <span className="text-caption text-fg-subtle">{overlap}</span> : null}
        </div>

        {showAccounts ? (
          <fieldset id={accountsId} className="grid gap-1.5 border-b border-border px-3.5 py-3">
            <legend className="sr-only">Add individual accounts</legend>
            {live.length === 0 ? (
              <p className="text-sm text-fg-muted">No connected accounts yet. Connect one on Connected Accounts first.</p>
            ) : (
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-x-3.5 gap-y-1.5">
                {live.map((a) => {
                  const inactive = a.status !== "active";
                  const covered = inChosenGroup.has(a.id);
                  return (
                    <li key={a.id}>
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          className="size-4 accent-[var(--accent)]"
                          checked={accountIds.includes(a.id)}
                          disabled={disabled || inactive}
                          onChange={() => setAccountIds((l) => toggle(l, a.id))}
                        />
                        <span className="min-w-0 truncate">
                          {a.nickname} <span className="text-fg-subtle">· {platformLabel(a.platform)}</span>
                          {inactive ? <span className="text-fg-subtle"> · not active</span> : covered ? <span className="text-fg-subtle"> · already via a group</span> : null}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </fieldset>
        ) : null}

        <PanelFoot className="flex flex-wrap items-center gap-2">
          {reachedAccounts.length > 0 ? (
            <>
              <AccountStack accounts={reachedAccounts.map((a) => ({ platform: platformIcon(a.platform), name: a.nickname }))} />
              <span>
                Drafts one post for each of {reachedAccounts.length} {reachedAccounts.length === 1 ? "account" : "accounts"}. An account
                reached through two groups still gets one.
              </span>
            </>
          ) : (
            <span>Pick groups, accounts or both. Each account gets its own post, written for it.</span>
          )}
        </PanelFoot>
        {errors.targets ? (
          <p id={targetsErrorId} className="flex items-center gap-1.5 px-3.5 pb-2.5 text-caption font-medium text-danger">
            <Icon name="alert" size="sm" />
            {errors.targets}
          </p>
        ) : null}
      </Panel>

      <Panel>
        <PanelHead title="What it's about" />
        <PanelBody className="grid gap-3.5">
          {useItem && item ? (
            <div className="grid gap-1.5 rounded-md border border-border bg-surface-muted p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Tag tone="info">From Discovery</Tag>
                {item.source_name ? <span className="text-caption text-fg-subtle">{item.source_name}</span> : null}
                <span className="flex-1" />
                <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setUseItem(false)}>
                  Write a topic brief instead
                </Button>
              </div>
              <p className="text-md font-semibold">
                {isWebUrl(item.url) ? (
                  <a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                    {item.title}
                    <span className="sr-only"> (opens in a new tab)</span>
                  </a>
                ) : (
                  item.title
                )}
              </p>
              {item.summary ? <p className="max-w-[78ch] text-sm text-fg-muted">{item.summary}</p> : null}
              {errors.content_item_id ? <p className="text-caption font-medium text-danger">{errors.content_item_id}</p> : null}
            </div>
          ) : (
            <Field
              label="Topic brief"
              hint="What the post should say. The Writer drafts each account's version from this."
              error={errors.topic_brief}
            >
              <Textarea value={topic} rows={4} maxLength={2000} disabled={disabled} onChange={(e) => setTopic(e.target.value)} />
            </Field>
          )}
          {!useItem && item ? (
            <div>
              <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setUseItem(true)}>
                Use “{item.title}” instead
              </Button>
            </div>
          ) : null}
          <Field label="Title (optional)" hint="Shown in lists. Defaults to the article's title or the start of the brief." error={errors.title}>
            <Input value={title} maxLength={200} disabled={disabled} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <div className="flex flex-wrap items-center gap-2.5">
            <Button type="submit" variant="primary" disabled={disabled}>
              {pending ? "Creating…" : "Create and draft"}
            </Button>
            <span className="text-caption text-fg-subtle">Drafting runs in the background; you can watch each account fill in.</span>
          </div>
        </PanelBody>
      </Panel>
    </form>
  );
}
