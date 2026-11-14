"use client";

import type { TemplateOut } from "@mera/sdk";

import type { AccountOut, GroupOut } from "@mera/sdk";
import Link from "next/link";
import { useState } from "react";
import { Topbar } from "@/components/shell/topbar";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { RelativeTime } from "@/components/ui/time";
import type { ContentItem, PostSet, Voice } from "@/lib/api/models";
import { asPostSetState, composerHref } from "@/lib/composer";
import { SCREENS } from "@/lib/nav";
import { describeStatus } from "@/lib/status";
import { NewPostForm } from "./new-post-form";
import { PostSetEditor } from "./post-set-editor";

/*
 * The Composer (composer.html). /composer opens the New post form (targets
 * and a subject, prefilled from ?item=<id>); /composer?set=<id> opens that
 * post set's editor. Recent drafts reopen sets. Editors and up write;
 * everyone else reads.
 */

export interface ComposerScreenProps {
  workspace: { id: string; slug: string; name: string; timezone: string; archived: boolean };
  groups: GroupOut[];
  accounts: AccountOut[];
  /** null when the voices couldn't be loaded. */
  voices: Voice[] | null;
  /** null when the list couldn't be loaded. */
  recent: PostSet[] | null;
  postSet: PostSet | null;
  /** ?set=<id> that didn't resolve. */
  missingSetId?: string;
  item: ContentItem | null;
  /** ?item=<id> that didn't resolve. */
  missingItemId?: string;
  /** Editor+ (EDIT_POSTS). */
  canManage: boolean;
  /** Post templates; null or absent when they couldn't be loaded. */
  templates?: TemplateOut[] | null;
  /** ?template=<id>. */
  initialTemplateId?: string;
  pollMs?: number;
}

function readClock(): number {
  return Date.now();
}

export function ComposerScreen({
  workspace,
  groups,
  accounts,
  voices,
  recent,
  postSet,
  missingSetId,
  item,
  missingItemId,
  canManage,
  templates,
  initialTemplateId,
  pollMs,
}: ComposerScreenProps) {
  const canEdit = canManage && !workspace.archived;
  const [now] = useState(readClock);
  const sub = postSet
    ? `${describeStatus("post_set", asPostSetState(postSet.state)).label} · ${postSet.variants.filter((v) => v.enabled).length} accounts`
    : "New post";

  return (
    <>
      <Topbar
        title={SCREENS.composer.title}
        sub={sub}
        action={postSet && canEdit ? { label: "New post", href: composerHref(workspace.slug) } : undefined}
      />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        {missingSetId ? (
          <Notice tone="warn" title="That post set isn't here.">
            It may have been archived, or it belongs to accounts outside your scope. Start a new post, or reopen one from Recent drafts.
          </Notice>
        ) : null}
        {missingItemId ? (
          <Notice tone="warn" title="That article isn't available any more.">
            Write a topic brief instead, or pick another item in Discovery.
          </Notice>
        ) : null}

        {postSet ? (
          <PostSetEditor
            key={postSet.id}
            workspace={workspace}
            postSet={postSet}
            accounts={accounts}
            voices={voices}
            canEdit={canEdit}
            pollMs={pollMs}
          />
        ) : canEdit ? (
          <NewPostForm
            workspace={workspace}
            groups={groups}
            accounts={accounts}
            item={item}
            canEdit={canEdit}
            templates={templates ?? []}
            initialTemplateId={initialTemplateId}
          />
        ) : (
          <Notice tone="default" title={workspace.archived ? "This workspace is archived." : "You can read drafts here, not write them."}>
            {workspace.archived
              ? "Nothing can be drafted until an owner restores it."
              : "Editors and up draft posts. Open a recent draft below to read it."}
          </Notice>
        )}

        <RecentDrafts slug={workspace.slug} tz={workspace.timezone} now={now} recent={recent} currentId={postSet?.id} />
      </main>
    </>
  );
}

function RecentDrafts({
  slug,
  tz,
  now,
  recent,
  currentId,
}: {
  slug: string;
  tz: string;
  now: number;
  recent: PostSet[] | null;
  currentId?: string;
}) {
  const list = (recent ?? []).filter((s) => s.id !== currentId);
  return (
    <Panel aria-labelledby="recent-drafts">
      <PanelHead>
        <h2 id="recent-drafts" className="font-label text-caption font-semibold tracking-[0.09em] uppercase">
          Recent drafts
        </h2>
      </PanelHead>
      {recent === null ? (
        <PanelBody>
          <p className="text-sm text-fg-muted">Couldn&apos;t load recent drafts. Reload to try again.</p>
        </PanelBody>
      ) : list.length === 0 ? (
        <PanelBody>
          <p className="text-sm text-fg-muted">No other drafts yet. Post sets you create show here.</p>
        </PanelBody>
      ) : (
        <ul>
          {list.map((s) => {
            const n = s.variants.filter((v) => v.enabled).length;
            return (
              <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3.5 py-2.5 last:border-b-0">
                <Link href={composerHref(slug, { set: s.id })} className="min-w-0 flex-1 truncate text-sm font-medium hover:underline">
                  {s.title}
                </Link>
                <span className="text-caption text-fg-subtle">
                  {n} {n === 1 ? "account" : "accounts"}
                </span>
                <StatusPill kind="post_set" state={asPostSetState(s.state)} />
                <RelativeTime value={s.updated_at} tz={tz} now={now} className="text-caption text-fg-subtle" />
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
