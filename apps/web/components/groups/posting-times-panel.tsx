"use client";

import { postingSlots, updateGroup, type GroupOut, type PostingSlotOut } from "@mera/sdk";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { Panel, PanelBody, PanelFoot, PanelHead } from "@/components/ui/panel";
import { Time } from "@/components/ui/time";
import { browserApi, settle } from "@/lib/api/browser";
import { displayMessage } from "@/lib/api/errors";

/*
 * P2-M1.6: the group's posting times. Approving without a requested time places
 * each account at its next free posting time plus its stagger offset; quiet hours,
 * the daily cap and spacing still apply, so times choose when, never loosen a rule.
 * An account's own times, set on the account, win over the group's.
 */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

interface Row {
  day: number;
  time: string;
}

function readRows(group: Pick<GroupOut, "posting_schedule">): Row[] {
  return (group.posting_schedule ?? []).map((t) => ({ day: t.day, time: t.time }));
}

export function PostingTimesPanel({
  group,
  workspaceId,
  timezone,
  canEdit,
}: {
  group: GroupOut;
  workspaceId: string;
  timezone: string;
  canEdit: boolean;
}) {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>(() => readRows(group));
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [slots, setSlots] = useState<PostingSlotOut[] | null>(null);

  useEffect(() => {
    let live = true;
    void settle(postingSlots({ client: browserApi, path: { workspace_id: workspaceId }, query: { group_id: group.id, days: 7 } })).then(
      (r) => {
        if (live) setSlots(r.ok ? r.data.data : []);
      },
    );
    return () => {
      live = false;
    };
  }, [workspaceId, group.id, group.posting_schedule]);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const bad = rows.find((r) => !HHMM.test(r.time));
    if (bad) {
      setError("Use 24-hour times like 09:30.");
      return;
    }
    const unique = [...new Map(rows.map((r) => [`${r.day}-${r.time}`, r])).values()].sort(
      (a, b) => a.day - b.day || a.time.localeCompare(b.time),
    );
    setPending(true);
    const r = await settle(
      updateGroup({
        client: browserApi,
        path: { workspace_id: workspaceId, group_id: group.id },
        body: { posting_schedule: unique.length ? unique : null },
      }),
    );
    setPending(false);
    if (!r.ok) {
      setError(displayMessage(r.error));
      return;
    }
    setRows(unique);
    router.refresh();
  }

  const disabled = !canEdit || pending;
  const next = (slots ?? []).filter((s) => !s.taken).slice(0, 6);

  return (
    <Panel>
      <PanelHead title="Posting times" />
      <PanelBody>
        <form onSubmit={save} noValidate className="grid gap-2.5" aria-label="Posting times">
          {error ? (
            <Notice tone="danger" role="alert">
              {error}
            </Notice>
          ) : null}
          {rows.length === 0 ? (
            <p className="text-caption text-fg-muted">No posting times: approved posts go out shortly after approval.</p>
          ) : null}
          <ul className="grid gap-1.5" aria-label="Times">
            {rows.map((row, i) => (
              <li key={i} className="flex items-center gap-2">
                <Select
                  aria-label={`Day for time ${i + 1}`}
                  value={row.day}
                  disabled={disabled}
                  onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, day: Number(e.target.value) } : r)))}
                  className="w-24"
                >
                  {DAYS.map((d, n) => (
                    <option key={d} value={n}>
                      {d}
                    </option>
                  ))}
                </Select>
                <Input
                  aria-label={`Time ${i + 1}`}
                  type="time"
                  value={row.time}
                  disabled={disabled}
                  onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, time: e.target.value } : r)))}
                  className="w-32"
                />
                <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label={`Remove time ${i + 1}`}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
          {canEdit ? (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="ghost" disabled={disabled} onClick={() => setRows([...rows, { day: 0, time: "09:00" }])}>
                Add a time
              </Button>
              <Button size="sm" variant="primary" type="submit" disabled={disabled}>
                {pending ? "Saving…" : "Save times"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={disabled}
                onClick={() => setRows(DAYS.slice(0, 5).map((_, d) => ({ day: d, time: "09:00" })))}
              >
                Weekdays 09:00
              </Button>
            </div>
          ) : null}
        </form>
      </PanelBody>
      <PanelFoot>
        {slots === null ? (
          "Loading the next free times…"
        ) : next.length === 0 ? (
          "No free posting time in the next 7 days."
        ) : (
          <span>
            Next free:{" "}
            {next.map((s, i) => (
              <span key={`${s.social_account_id}-${s.at}`}>
                {i > 0 ? ", " : ""}
                <Time value={s.at} tz={timezone} format="datetime" /> {s.account_nickname}
              </span>
            ))}
          </span>
        )}
      </PanelFoot>
    </Panel>
  );
}
