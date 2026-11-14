import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ComposerScreen } from "@/components/composer/composer-screen";
import {
  getAccounts,
  getContentItemById,
  getGroups,
  getPostSet,
  getRecentPostSets,
  getSession,
  getTemplates, getVoices,
  optional,
} from "@/lib/api/server";
import { RECENT_DRAFTS_LIMIT } from "@/lib/composer";
import { SCREENS } from "@/lib/nav";
import { findBySlug, isEditorRole } from "@/lib/workspace";

export const metadata: Metadata = { title: SCREENS.composer.title };

/*
 * The Composer (composer.html). Live since Sprint 6 (M5): ?set=<id> opens a
 * post set's per-account editor (versions, rewrites, hashtags, preview,
 * guardrails); without it, a New post form whose subject can be prefilled
 * from ?item=<content_item_id>. Editors and up write (EDIT_POSTS). Media
 * upload is Sprint 7; scheduling M8; sending for approval M6.
 */
export default async function ComposerPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const query = await searchParams;
  const setId = typeof query.set === "string" && query.set ? query.set : undefined;
  const itemId = typeof query.item === "string" && query.item ? query.item : undefined;
  const templateId = typeof query.template === "string" && query.template ? query.template : undefined;
  const here = `/w/${slug}/composer${setId ? `?set=${encodeURIComponent(setId)}` : itemId ? `?item=${encodeURIComponent(itemId)}` : ""}`;
  const session = await getSession(here);
  const workspace = findBySlug(session.workspaces, slug);
  if (!workspace) notFound();

  // The set (or the form's targets) is the page; recent drafts and voices degrade on their own.
  const [postSet, item, groups, accounts, recent, voices, templates] = await Promise.all([
    setId ? getPostSet(workspace.id, setId, here) : Promise.resolve(null),
    !setId && itemId ? optional(getContentItemById(workspace.id, itemId, here)) : Promise.resolve(null),
    getGroups(workspace.id, here),
    getAccounts(workspace.id, here),
    optional(getRecentPostSets(workspace.id, RECENT_DRAFTS_LIMIT, here)),
    optional(getVoices(workspace.id, here)),
    setId ? Promise.resolve(null) : optional(getTemplates(workspace.id, here)),
  ]);

  return (
    <ComposerScreen
      key={postSet?.id ?? "new"}
      workspace={{
        id: workspace.id,
        slug: workspace.slug,
        name: workspace.name,
        timezone: workspace.timezone,
        archived: workspace.archived,
      }}
      groups={groups}
      accounts={accounts}
      voices={voices ?? null}
      recent={recent ?? null}
      postSet={postSet}
      missingSetId={setId && !postSet ? setId : undefined}
      item={item ?? null}
      missingItemId={!setId && itemId && !item ? itemId : undefined}
      canManage={isEditorRole(workspace.role)}
      templates={templates ?? null}
      initialTemplateId={templateId}
    />
  );
}
