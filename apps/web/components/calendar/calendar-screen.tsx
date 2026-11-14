"use client";

import { calendarMove, cancelScheduled, publishNow, retry, resolveDeadLetter, type DeadLetterOut, type ScheduledPostOut } from "@mera/sdk";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, type DragEvent } from "react";
import { AccountChip } from "@/components/mera/account-chip";
import { GroupDot } from "@/components/mera/group-dot";
import { Topbar } from "@/components/shell/topbar";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input, Label } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelHead } from "@/components/ui/panel";
import { StatusPill } from "@/components/ui/status-pill";
import { Time } from "@/components/ui/time";
import { useWorkspaceEvents } from "@/hooks/use-workspace-events";
import { platformIcon } from "@/lib/accounts";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";
import {
  addDays,
  asScheduledState,
  byDay,
  canMove,
  dayLabel,
  localDate,
  localTime,
  moveToDay,
  publishStateLabel,
  weekDays,
  weekRangeLabel,
  zonedToUtc,
} from "@/lib/calendar";
import { composerHref } from "@/lib/composer";
import { SCREENS } from "@/lib/nav";

/*
 * Content Calendar (calendar.html, M8.12). A week of slots in the workspace time zone,
 * colour-coded by the group each post goes out through. Moving a post: drag it to
 * another day (keeps its time), or open it and pick a date and time: the keyboard path.
 * The API still applies quiet hours, the daily cap and spacing, and says if it moved
 * the slot. Published posts link out; failed ones can be retried here.
 */

export interface CalendarScreenProps {
  workspace: { id: string; slug: string; timezone: string; archived: boolean };
  week: string; // Monday, YYYY-MM-DD
  items: ScheduledPostOut[];
  deadLetters: DeadLetterOut[];
  groups: { id: string; name: string }[];
  groupId: string | null;
}

export function CalendarScreen({ workspace, week, items: fromServer, deadLetters, groups, groupId }: CalendarScreenProps) {
  const router = useRouter();
  const tz = workspace.timezone;
  const [state, setState] = useState({ fromServer, items: fromServer });
  if (state.fromServer !== fromServer) setState({ fromServer, items: fromServer });
  const items = state.items;
  const [flash, setFlash] = useState<{ tone: "info" | "danger"; text: string } | null>(null);
  const [open, setOpen] = useState<ScheduledPostOut | null>(null);
  useWorkspaceEvents(workspace.id, ["post.*", "post_set.*", "approval.granted"]);

  const days = weekDays(week);
  const buckets = byDay(items, tz);
  const today = localDate(new Date(), tz);
  const href = (w: string, g: string | null = groupId) =>
    `/w/${workspace.slug}/calendar?week=${w}${g ? `&group=${encodeURIComponent(g)}` : ""}`;

  function put(next: ScheduledPostOut) {
    setState((s) => ({ ...s, items: s.items.map((i) => (i.id === next.id ? next : i)) }));
  }

  async function move(item: ScheduledPostOut, when: Date) {
    setFlash(null);
    const r = await settle(
      calendarMove({
        client: browserApi,
        path: { workspace_id: workspace.id, sp_id: item.id },
        body: { scheduled_for: when.toISOString() },
      }),
    );
    if (!r.ok) {
      setFlash({ tone: "danger", text: displayMessage(r.error) });
      return false;
    }
    put(r.data);
    const moved = r.data.moved_by.length ? ` The slot moved for ${r.data.moved_by.join(", ")}.` : "";
    setFlash({ tone: "info", text: `${r.data.account_nickname} now posts ${localDate(r.data.scheduled_for, tz)} at ${localTime(r.data.scheduled_for, tz)}.${moved}` });
    router.refresh();
    return true;
  }

  async function act(item: ScheduledPostOut, kind: "now" | "cancel" | "retry") {
    setFlash(null);
    const path = { workspace_id: workspace.id, sp_id: item.id };
    const call =
      kind === "now"
        ? publishNow({ client: browserApi, path })
        : kind === "cancel"
          ? cancelScheduled({ client: browserApi, path })
          : retry({ client: browserApi, path });
    const r = await settle(call);
    if (!r.ok) {
      setFlash({ tone: "danger", text: displayMessage(r.error) });
      return;
    }
    put(r.data);
    setOpen(null);
    setFlash({
      tone: "info",
      text:
        kind === "now"
          ? `Publishing ${r.data.account_nickname} now.`
          : kind === "cancel"
            ? `Cancelled ${r.data.account_nickname}. Schedule the set again to put it back.`
            : `Retrying ${r.data.account_nickname}; every publish check runs again.`,
    });
    router.refresh();
  }

  function onDrop(e: DragEvent<HTMLLIElement>, day: string) {
    e.preventDefault();
    const id = e.dataTransfer.getData("text/mera-slot");
    const item = items.find((i) => i.id === id);
    if (!item || !canMove(item.state) || localDate(item.scheduled_for, tz) === day) return;
    void move(item, moveToDay(item.scheduled_for, day, tz));
  }

  const failing = deadLetters.filter((d) => !d.resolved_at);

  return (
    <>
      <Topbar title={SCREENS.calendar.title} sub={`${weekRangeLabel(week)} · ${tz}`} action={{ label: "New post", target: "composer" }} />
      <main id="main" tabIndex={-1} className="grid flex-1 content-start gap-3.5 p-6 outline-none max-rail:p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild size="sm" variant="ghost">
            <Link href={href(addDays(week, -7))}>← Previous week</Link>
          </Button>
          <Button asChild size="sm" variant="ghost">
            <Link href={href(addDays(week, 7))}>Next week →</Link>
          </Button>
          <span className="flex-1" />
          <nav aria-label="Filter by group" className="flex flex-wrap items-center gap-1.5 text-caption">
            <Link href={href(week, null)} aria-current={groupId === null ? "page" : undefined} className="rounded-sm px-2 py-1 aria-[current=page]:bg-active">
              All groups
            </Link>
            {groups.map((g) => (
              <Link key={g.id} href={href(week, g.id)} aria-current={groupId === g.id ? "page" : undefined} className="flex items-center gap-1.5 rounded-sm px-2 py-1 aria-[current=page]:bg-active">
                <GroupDot group={g.id} /> {g.name}
              </Link>
            ))}
          </nav>
        </div>

        <div role="status" aria-live="polite" className="empty:hidden">
          {flash?.tone === "info" ? <Notice tone="info" icon="check">{flash.text}</Notice> : null}
        </div>
        {flash?.tone === "danger" ? <Notice tone="danger" role="alert">{flash.text}</Notice> : null}

        {failing.length ? (
          <Notice tone="danger" title={`${failing.length} post${failing.length === 1 ? "" : "s"} failed and will not retry on ${failing.length === 1 ? "its" : "their"} own`}>
            <ul className="mt-1 grid gap-1">
              {failing.map((d) => (
                <li key={d.id} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{d.account_nickname ?? "Account"}</span>
                  <span className="text-fg-muted">· {d.post_set_title ?? "post"} · “{d.error}”</span>
                  <span className="flex-1" />
                  <Button size="sm" disabled={workspace.archived} onClick={() => void act({ id: d.subject_id } as ScheduledPostOut, "retry")}>
                    Retry
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      const r = await settle(resolveDeadLetter({ client: browserApi, path: { workspace_id: workspace.id, dl_id: d.id } }));
                      if (r.ok) router.refresh();
                    }}
                  >
                    Dismiss
                  </Button>
                </li>
              ))}
            </ul>
          </Notice>
        ) : null}

        <Panel>
          <PanelBody className="p-0">
            <ol className="grid grid-cols-7 divide-x divide-border max-[1100px]:grid-cols-1 max-[1100px]:divide-x-0 max-[1100px]:divide-y" aria-label={`Week of ${dayLabel(week)}`}>
              {days.map((day) => {
                const list = buckets.get(day) ?? [];
                return (
                  <li
                    key={day}
                    className="min-h-48 min-w-0 p-2"
                    aria-label={dayLabel(day)}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => onDrop(e, day)}
                  >
                    <h2 className={`mb-2 font-label text-caption font-semibold tracking-[0.06em] uppercase ${day === today ? "text-accent" : "text-fg-subtle"}`}>
                      {dayLabel(day)}
                      {day === today ? <span className="sr-only"> (today)</span> : null}
                    </h2>
                    {list.length === 0 ? <p className="text-caption text-fg-subtle">Nothing scheduled</p> : null}
                    <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">
                      {list.map((it) => (
                        <li key={it.id}>
                          <button
                            type="button"
                            draggable={canMove(it.state) && !workspace.archived}
                            onDragStart={(e) => e.dataTransfer.setData("text/mera-slot", it.id)}
                            onClick={() => setOpen(it)}
                            className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)] gap-1 rounded-md border border-border bg-raised p-2 text-left text-caption hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent"
                            aria-label={`${localTime(it.scheduled_for, tz)} ${it.account_nickname}, ${publishStateLabel(it.state, it.attempt_count, it.max_attempts)}. Open to reschedule.`}
                          >
                            <span className="flex min-w-0 items-center gap-1.5">
                              {it.account_group_id ? <GroupDot group={it.account_group_id} /> : null}
                              <span className="shrink-0 font-mono">{localTime(it.scheduled_for, tz)}</span>
                              <span className="truncate font-medium">{it.account_nickname}</span>
                            </span>
                            <StatusPill kind="scheduled_post" state={asScheduledState(it.state)} label={publishStateLabel(it.state, it.attempt_count, it.max_attempts)} />
                            <span className="line-clamp-2 text-fg-muted">{it.excerpt}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </li>
                );
              })}
            </ol>
          </PanelBody>
        </Panel>
        {items.length === 0 ? (
          <EmptyState
            icon="calendar"
            title="Nothing on the calendar this week"
            description="Approved posts land here, one slot per account, staggered."
            action={<Button asChild size="sm"><Link href={`/w/${workspace.slug}/composer`}>New post</Link></Button>}
          />
        ) : null}
        {open ? (
          <SlotDialog item={open} tz={tz} archived={workspace.archived} slug={workspace.slug} onClose={() => setOpen(null)} onMove={move} onAct={act} />
        ) : null}
      </main>
    </>
  );
}

function SlotDialog({
  item,
  tz,
  archived,
  slug,
  onClose,
  onMove,
  onAct,
}: {
  item: ScheduledPostOut;
  tz: string;
  archived: boolean;
  slug: string;
  onClose: () => void;
  onMove: (i: ScheduledPostOut, when: Date) => Promise<boolean>;
  onAct: (i: ScheduledPostOut, k: "now" | "cancel" | "retry") => Promise<void>;
}) {
  const id = useId();
  const [date, setDate] = useState(localDate(item.scheduled_for, tz));
  const [time, setTime] = useState(localTime(item.scheduled_for, tz));
  const movable = canMove(item.state) && !archived;
  return (
    <Panel role="dialog" aria-modal="false" aria-labelledby={`${id}-t`} className="fixed inset-x-4 bottom-4 z-40 mx-auto max-w-xl shadow-popover">
      <PanelHead
        title={<span id={`${id}-t`}>{item.account_nickname}</span>}
        actions={<Button size="sm" variant="ghost" onClick={onClose}>Close</Button>}
      />
      <PanelBody className="grid gap-3">
        <AccountChip account={{ platform: platformIcon(item.platform as never), name: item.account_nickname, handle: item.account_handle }} />
        <p className="text-sm">
          <Link className="font-medium hover:underline" href={composerHref(slug, { set: item.post_set_id })}>{item.post_set_title}</Link>
        </p>
        <p className="text-sm whitespace-pre-wrap text-fg-muted">{item.excerpt}</p>
        <p className="text-caption text-fg-subtle">
          <Time value={item.scheduled_for} tz={tz} format="datetime" /> · stagger offset {Math.round(item.stagger_offset_seconds / 60)} min
          {item.moved_by.length ? ` · moved for ${item.moved_by.join(", ")}` : ""}
        </p>
        {item.last_error ? <Notice tone={item.state === "dead_lettered" ? "danger" : "warn"}>{item.last_error}</Notice> : null}
        {item.permalink ? (
          <a className="text-sm font-medium text-accent hover:underline" href={item.permalink} target="_blank" rel="noreferrer">
            View the published post
          </a>
        ) : null}
        {movable ? (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              if (await onMove(item, zonedToUtc(date, time, tz))) onClose();
            }}
          >
            <div className="grid gap-1">
              <Label htmlFor={`${id}-d`}>Date</Label>
              <Input id={`${id}-d`} type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
            </div>
            <div className="grid gap-1">
              <Label htmlFor={`${id}-h`}>Time ({tz})</Label>
              <Input id={`${id}-h`} type="time" value={time} onChange={(e) => setTime(e.target.value)} required />
            </div>
            <Button type="submit" size="sm" variant="primary">Reschedule</Button>
          </form>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {movable ? <Button size="sm" onClick={() => void onAct(item, "now")}>Publish now</Button> : null}
          {movable ? <Button size="sm" variant="ghost" onClick={() => void onAct(item, "cancel")}>Cancel this post</Button> : null}
          {(item.state === "dead_lettered" || item.state === "failed") && !archived ? (
            <Button size="sm" onClick={() => void onAct(item, "retry")}>Retry now</Button>
          ) : null}
        </div>
      </PanelBody>
    </Panel>
  );
}
