"use client";

import { listPlatforms, platformSkill, type PlatformOut, type SkillTextOut } from "@mera/sdk";
import { useEffect, useId, useState } from "react";
import { PlatformMark } from "@/components/icons";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { Tag } from "@/components/ui/tag";
import { platformIcon } from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";

/*
 * Platform skills: the writing playbook each Phase 1 platform's agents follow,
 * vendored from upstream at a pinned commit and checksum-verified before use.
 * Read-only here: what shapes a draft is reviewed when imported, never edited
 * in the app. "View" shows exactly the text an agent receives (tool and
 * publishing instructions already removed).
 */

const ROLE_LABEL: Record<string, string> = {
  repurposer: "From an article",
  writer: "From a brief",
  humanizer: "Editor checklist",
  hashtags: "Hashtags",
  carousel: "Carousels",
  thread: "Threads",
};

const LINK_LABEL: Record<string, string> = {
  body: "Link in the post",
  first_comment: "Link as first comment",
  reply: "Link as a reply",
  none: "No link; source named",
};

export function SkillsPanel() {
  const id = useId();
  const [platforms, setPlatforms] = useState<PlatformOut[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<{ platform: string; role: string } | null>(null);
  const [text, setText] = useState<SkillTextOut | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let live = true;
    void settle(listPlatforms({ client: browserApi })).then((r) => {
      if (!live) return;
      if (r.ok) setPlatforms(r.data.data.filter((p) => p.phase === 1));
      else setFailed(true);
    });
    return () => {
      live = false;
    };
  }, []);

  async function view(platform: string, role: string) {
    if (open?.platform === platform && open.role === role) {
      setOpen(null);
      return;
    }
    setOpen({ platform, role });
    setText(null);
    setLoading(true);
    const r = await settle(platformSkill({ client: browserApi, path: { platform: platform as PlatformOut["platform"], role } }));
    setLoading(false);
    if (r.ok) setText(r.data);
  }

  return (
    <Panel aria-labelledby={`${id}-head`}>
      <PanelHead>
        <h2 id={`${id}-head`} className="text-md font-semibold">
          Platform skills
        </h2>
        <span className="text-caption text-fg-subtle">How each platform&apos;s post is built</span>
      </PanelHead>
      <PanelBody className="grid gap-2.5">
        {failed ? <Notice tone="danger">The skills catalogue could not be loaded.</Notice> : null}
        {!platforms && !failed ? <Skeleton className="h-24" /> : null}
        {platforms?.map((p) => (
          <div key={p.platform} className="grid gap-2 rounded-md border border-border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <PlatformMark platform={platformIcon(p.platform)} size="sm" decorative />
              <span className="font-semibold">{p.display_name}</span>
              <Tag tone="outline" className="font-sans">
                {LINK_LABEL[p.link_placement] ?? p.link_placement}
              </Tag>
              {p.requires_media ? <Tag tone="warn">Image required</Tag> : null}
              <span className="flex-1" />
              {p.skill ? (
                <a
                  href={`${p.skill.repo}/tree/${p.skill.commit}`}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-mono text-caption text-fg-subtle underline-offset-2 hover:underline"
                >
                  {p.skill.name}@{p.skill.commit.slice(0, 7)} · {p.skill.licence}
                </a>
              ) : (
                <span className="text-caption text-fg-subtle">No skill vendored</span>
              )}
            </div>
            {p.skill ? (
              <div className="flex flex-wrap gap-1.5">
                {p.skill.roles.map((r) => {
                  const active = open?.platform === p.platform && open.role === r.role;
                  return (
                    <Button
                      key={r.role}
                      size="sm"
                      variant={active ? "primary" : "default"}
                      aria-expanded={active}
                      aria-controls={active ? `${id}-text` : undefined}
                      title={`${r.skill} · used by ${r.used_by}`}
                      onClick={() => void view(p.platform, r.role)}
                    >
                      {ROLE_LABEL[r.role] ?? r.role}
                    </Button>
                  );
                })}
              </div>
            ) : null}
            {open?.platform === p.platform ? (
              <div id={`${id}-text`} className="grid gap-1.5">
                {loading ? <Skeleton className="h-32" /> : null}
                {text ? (
                  <>
                    <p className="text-caption text-fg-subtle">
                      {text.skill}: exactly what the agent receives. Mera&apos;s own rules (grounding, crediting the source, no
                      invented numbers) win over anything here.
                    </p>
                    <pre className="max-h-96 overflow-auto rounded-md bg-surface-sunken p-3 text-xs leading-relaxed whitespace-pre-wrap">
                      {text.text}
                    </pre>
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
        ))}
      </PanelBody>
      <PanelFoot>Skills are pinned to an upstream commit and checked against a checksum before every use.</PanelFoot>
    </Panel>
  );
}
